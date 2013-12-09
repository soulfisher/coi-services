#!/usr/bin/env python

"""
DatasetAgent: generalized implementation of dataset agent:
- generalized ion interface following the same pattern as instrument agent.
- pluggable behavior to load specialized driver code.
- hands-off interrupt/resume state mechanism: memento generated by poller only has to make sense to that poller

classes defined:
- DatasetAgent
    - abstract base class
    - implements two-state model (streaming or idle)
    - acts as agent, driver client, and driver; simplifies control flow

interrupt/resume state:
- on each callback, the poller provides a memento it can use to keep its position after resume
- after successful parsing, the memento is persisted as part of the agent state
- upon restart, the agent reads the memento and passes to the driver
"""

__author__ = 'Christopher Mueller, Jonathan Newbrough, Bill French'


import os, sys, gevent, json, math, time

from ooi.logging import log
from ooi.poller import DirectoryPoller
from ooi.reflection import EggCache

from pyon.agent.agent import ResourceAgentEvent
from pyon.agent.agent import ResourceAgentState
from pyon.core.exception import InstStateError
from pyon.public import OT
from pyon.core.bootstrap import IonObject
from pyon.util.containers import get_safe
from pyon.ion.stream import StandaloneStreamPublisher

from ion.agents.instrument.exceptions import InstrumentStateException
from ion.agents.instrument.common import BaseEnum
from ion.agents.instrument.instrument_agent import InstrumentAgent
from ion.core.includes.mi import DriverEvent
from ion.services.dm.utility.granule.record_dictionary import RecordDictionaryTool

from coverage_model import ParameterDictionary

# TODO: make unique for multiple processes on same VM
EGG_CACHE_DIR='/tmp/eggs%d' % os.getpid()
EGG_CACHE=EggCache(EGG_CACHE_DIR)
DSA_STATE_KEY = 'dsa_state'

class DataSetAgentCapability(BaseEnum):
    INITIALIZE = ResourceAgentEvent.INITIALIZE
    RESET = ResourceAgentEvent.RESET
    GO_ACTIVE = ResourceAgentEvent.GO_ACTIVE
    GO_INACTIVE = ResourceAgentEvent.GO_INACTIVE
    RUN = ResourceAgentEvent.RUN
    CLEAR = ResourceAgentEvent.CLEAR
    PAUSE = ResourceAgentEvent.PAUSE
    RESUME = ResourceAgentEvent.RESUME
    GO_COMMAND = ResourceAgentEvent.GO_COMMAND

class DataSetAgent(InstrumentAgent):
    """
    this dataset agent has two states: autosampling and idle
    based on InstrumentAgent but override methods to simplify flow control
    generalized ion interface, specialization provided by the driver class.
    """
    ORIGIN_TYPE = "Dataset"

    def __init__(self, *args, **kwargs):
        super(DataSetAgent,self).__init__(*args, **kwargs)

        log.debug("Agent: __init__")

        self._fsm.add_handler(ResourceAgentState.STREAMING, ResourceAgentEvent.ENTER, self._handler_streaming_enter)
        self._fsm.add_handler(ResourceAgentState.STREAMING, ResourceAgentEvent.EXIT, self._handler_streaming_exit)

        self._retry_calculator = FactorialRetryCalculator(60, 1.5, 3600)

        # simulate compliance with platform agent - dict of aggregate statuses for all descendants
        self.aparam_child_agg_status = {}

    ####
    ##    Response Handlers
    ####
    def _handler_active_unknown_go_inactive(self, *args, **kwargs):
        return (ResourceAgentState.INACTIVE, None)

    def _handler_inactive_go_active(self, *args, **kwargs):
        return (ResourceAgentState.IDLE, None)

    def _handler_streaming_enter(self, *args, **kwargs):
        super(DataSetAgent, self)._common_state_enter(*args, **kwargs)
        self._dvr_client.start_sampling()

    def _handler_streaming_exit(self, *args, **kwargs):
        super(DataSetAgent, self)._common_state_exit(*args, **kwargs)
        self._dvr_client.stop_sampling()

    def _handler_get_resource_capabilities(self, *args, **kwargs):
        """
        """
        next_state = None
        result = None

        try:
            next_state = None
            result = self._dvr_client.cmd_dvr('get_resource_capabilities', *args, **kwargs)
        except Exception as e:
            log.error("get_capabilities exception: %s", e)

        return (next_state, result)

    ####
    ##    Recovery logic
    ####
    def _handler_lost_connection_enter(self, *args, **kwargs):
        """
        Enter a state where we have trapped an exception from a driver.
        We assume that the driver was in or heading to streaming mode
        when the exception was raised.
        """
        super(DataSetAgent, self)._common_state_enter(*args, **kwargs)
        log.error('Dataset agent %s lost connection to the device.',
                  self._proc_name)

        self._event_publisher.publish_event(
            event_type='ResourceAgentConnectionLostErrorEvent',
            origin_type=self.ORIGIN_TYPE,
            origin=self.resource_id)

        # Setup reconnect timer.
        self._autoreconnect_greenlet = gevent.spawn(self._autoreconnect)

    def _autoreconnect(self):
        """
        Retry logic.  Uses a factorial sequence to determine retry interval.
        Retry interval will cap out at 60 minutes.

        We will consider a subsequent failure when the last retry is less
        than double the time we expect the error to have been cleared.

        The retry time will be the factorial se
        """
        while self._autoreconnect_greenlet:
            gevent.sleep(self._retry_calculator.get_sleep_time())
            try:
                self._fsm.on_event(ResourceAgentEvent.AUTORECONNECT)
            except:
                pass

    def _handler_lost_connection__autoreconnect(self, *args, **kwargs):
        """
        This handler is called when the driver raises an exception when
        in streaming mode only.  This is important because the driver
        does not track a resource state like the instruments can.  So
        we have to assume that the driver only raises exceptions that are
        retryable when raised from streaming.
        """
        # Reset the connection id and index.
        self._asp.reset_connection()

        if(self._state_when_lost in [ResourceAgentState.STREAMING, ResourceAgentState.COMMAND]):
            log.debug("Exception detected from driver operation, attempting to reconnect.")

            (next_state, result) = self._dvr_client.cmd_dvr('execute_resource', DriverEvent.START_AUTOSAMPLE)
            log.debug("_handler_lost_connection__autoreconnect: start autosample result: %s, %s", next_state, result)

        else:
            log.debug("Exception detected during agent startup. going back to %s", self._state_when_lost)
            next_state = self._state_when_lost

        return (next_state, None)

    ####
    ##    Helpers
    ####
    def _create_driver_plugin(self):
        try:
            # Ensure the egg cache directory exists. ooi.reflections will fail
            # somewhat silently when this directory doesn't exists.
            if not os.path.isdir(EGG_CACHE_DIR):
                os.makedirs(EGG_CACHE_DIR)

            log.debug("getting plugin config")
            uri = get_safe(self._dvr_config, 'dvr_egg')
            module_name = self._dvr_config['dvr_mod']
            class_name = self._dvr_config['dvr_cls']
            config = self._dvr_config['startup_config']
        except:
            log.error('error in configuration', exc_info=True)
            raise

        egg_name = None
        egg_repo = None

        memento = self._get_state(DSA_STATE_KEY)

        if memento:
            # memento not empty, which is the case after restart. Just keep what we have.
            log.info("Using process persistent state: %s", memento)
        else:
            # memento empty, which is the case after a fresh start. See if we got stuff in CFG

            # Set state based on CFG using prior process' state
            prior_state = self.CFG.get_safe("agent.prior_state")
            if prior_state:
                if isinstance(prior_state, dict):
                    if DSA_STATE_KEY in prior_state:
                        memento = prior_state[DSA_STATE_KEY]
                        log.info("Using persistent state from prior agent run: %s", memento)
                        self.persist_state_callback(memento)
                else:
                    raise InstrumentStateException('agent.prior_state invalid: %s' % prior_state)

        log.warn("Get driver object: %s, %s, %s, %s, %s", class_name, module_name, egg_name, egg_repo, memento)
        if uri:
            egg_name = uri.split('/')[-1] if uri.startswith('http') else uri
            egg_repo = uri[0:len(uri)-len(egg_name)-1] if uri.startswith('http') else None

        log.info("instantiate driver plugin %s.%s", module_name, class_name)
        params = [config, memento, self.publish_callback, self.persist_state_callback, self.exception_callback]
        return EGG_CACHE.get_object(class_name, module_name, egg_name, egg_repo, params)


    def _validate_driver_config(self):
        """
        Verify the agent configuration contains a driver config.  called by uninitialize_initialize handler
        in the IA class
        """
        log.debug("Driver Config: %s", self._dvr_config)
        out = True

        for key in ('startup_config', 'dvr_mod', 'dvr_cls'):
            if key not in self._dvr_config:
                log.error('missing key: %s', key)
                out = False

        for key in ('stream_config', ):
            if key not in self.CFG:
                log.error('missing key: %s', key)
                out = False

        if get_safe(self._dvr_config, 'max_records', 100) < 1:
            log.error('max_records=%d, must be at least 1 or unset (default 100)', self.max_records)
            out = False

        return out

    def _start_driver(self, dvr_config):
        log.warn("DRIVER: _start_driver: %s", dvr_config)
        self._dvr_client = self._create_driver_plugin()

        if self._dvr_client == None:
            log.error("Failed to instantiate driver plugin!")
            raise InstrumentStateException('failed to start driver')

        log.warn("driver client created")

        self._asp.reset_connection()

    def _stop_driver(self, force=True):
        log.warn("DRIVER: _stop_driver")
        if self._dvr_client:
            self._dvr_client.stop_sampling()

    ####
    ##    Callbacks
    ####
    def persist_state_callback(self, driver_state):
        log.debug("Saving driver state: %r", driver_state)
        self._set_state(DSA_STATE_KEY, driver_state)

    def publish_callback(self, particle):
        """
        Publish particles to the agent.

        TODO: currently we are generating JSON serialized objects
        we should be able to send with objects because we don't have
        the zmq boundray issue in this client.

        @return: number of records published
        """
        publish_count = 0
        try:
            for p in particle:
                # Can we use p.generate_dict() here?
                p_obj = p.generate()
                log.info("Particle received: %s", p_obj)
                self._async_driver_event_sample(p_obj, None)
                publish_count += 1
        except Exception as e:
            log.error("Error logging particle: %s", e, exc_info=True)

            # Reset the connection id because we can not ensure contiguous
            # data.
            self._asp.reset_connection()

            log.debug("Publish ResourceAgentErrorEvent from publisher_callback")
            self._event_publisher.publish_event(
                error_msg = "Sample Parsing Exception: %s" % e,
                event_type='ResourceAgentErrorEvent',
                origin_type=self.ORIGIN_TYPE,
                origin=self.resource_id
            )

        return publish_count

    def exception_callback(self, exception):
        """
        Callback passed to the driver which handles exceptions raised when
        in streaming mode.
        """
        log.error('Exception detected in the driver', exc_info=True)
        self._fsm.on_event(ResourceAgentEvent.LOST_CONNECTION)

    def on_quit(self):
        super(DataSetAgent, self).on_quit()
        self._stop_pinger()

        self._aam.stop_all()

        params = {}
        for (k,v) in self.aparam_pubrate.iteritems():
            if v > 0:
                params[k] = 0

        if len(params)>0:
            self.aparam_set_pubrate(params)

        state = self._fsm.get_current_state()
        if state == ResourceAgentState.UNINITIALIZED:
            pass

        elif state == ResourceAgentState.INACTIVE:
            self._stop_driver()

        else:
            try:
                if self._dvr_client:
                    self._dvr_client.stop_sampling()
            except:
                self._stop_driver()

    def _async_driver_event_sample(self, val, ts):
        """
        Publish sample on sample data streams.
        """
        # If the sample event is encoded, load it back to a dict.
        if isinstance(val, str):
            val = json.loads(val)

        new_sequence = val.get('new_sequence')

        if new_sequence == True:
            log.info("New sequence flag detected in particle.  Resetting connection ID")
            self._asp.reset_connection()

        super(DataSetAgent, self)._async_driver_event_sample(val, ts)

    def _filter_capabilities(self, events):
        events_out = [x for x in events if DataSetAgentCapability.has(x)]
        return events_out

    def _restore_resource(self, state, prev_state):
        """
        Restore agent/resource configuration and state.
        """
        log.debug("starting agent restore process, State: %s, Prev State: %s", state, prev_state)

        # Get state to restore. If the last state was lost connection,
        # use the prior connected state.
        if not state:
            log.debug("State not defined, not restoring")
            return

        if state == ResourceAgentState.LOST_CONNECTION:
            state = prev_state

        try:
            cur_state = self._fsm.get_current_state()

            # If unitialized, confirm and do nothing.
            if state == ResourceAgentState.UNINITIALIZED:
                if cur_state != state:
                    raise Exception()

            # If inactive, initialize and confirm.
            elif state == ResourceAgentState.INACTIVE:
                self._fsm.on_event(ResourceAgentEvent.INITIALIZE)
                cur_state = self._fsm.get_current_state()
                if cur_state != state:
                    raise Exception()

            # If idle, initialize, activate and confirm.
            elif state == ResourceAgentState.IDLE:
                self._fsm.on_event(ResourceAgentEvent.INITIALIZE)
                self._fsm.on_event(ResourceAgentEvent.GO_ACTIVE)
                cur_state = self._fsm.get_current_state()
                if cur_state != state:
                    raise Exception()

            # If streaming, initialize, activate and confirm.
            # Driver discover should put us in streaming mode.
            elif state == ResourceAgentState.STREAMING:
                self._fsm.on_event(ResourceAgentEvent.INITIALIZE)
                self._fsm.on_event(ResourceAgentEvent.GO_ACTIVE)
                self._fsm.on_event(ResourceAgentEvent.RUN)
                self._fsm.on_event(ResourceAgentEvent.EXECUTE_RESOURCE, DriverEvent.START_AUTOSAMPLE)
                cur_state = self._fsm.get_current_state()
                if cur_state != state:
                    raise Exception()

            # If command, initialize, activate, confirm idle,
            # run and confirm command.
            elif state == ResourceAgentState.COMMAND:
                self._fsm.on_event(ResourceAgentEvent.INITIALIZE)
                self._fsm.on_event(ResourceAgentEvent.GO_ACTIVE)
                cur_state = self._fsm.get_current_state()
                if cur_state != ResourceAgentState.IDLE:
                    raise Exception()
                self._fsm.on_event(ResourceAgentEvent.RUN)
                cur_state = self._fsm.get_current_state()
                if cur_state != state:
                    raise Exception()

            # If paused, initialize, activate, confirm idle,
            # run, confirm command, pause and confirm stopped.
            elif state == ResourceAgentState.STOPPED:
                self._fsm.on_event(ResourceAgentEvent.INITIALIZE)
                self._fsm.on_event(ResourceAgentEvent.GO_ACTIVE)
                cur_state = self._fsm.get_current_state()
                if cur_state != ResourceAgentState.IDLE:
                    raise Exception()
                self._fsm.on_event(ResourceAgentEvent.RUN)
                cur_state = self._fsm.get_current_state()
                if cur_state != ResourceAgentState.COMMAND:
                    raise Exception()
                self._fsm.on_event(ResourceAgentEvent.PAUSE)
                cur_state = self._fsm.get_current_state()
                if cur_state != state:
                    raise Exception()

            # If in a command reachable substate, attempt to return to command.
            # Initialize, activate, confirm idle, run confirm command.
            elif state in [ResourceAgentState.TEST,
                    ResourceAgentState.CALIBRATE,
                    ResourceAgentState.DIRECT_ACCESS,
                    ResourceAgentState.BUSY]:
                self._fsm.on_event(ResourceAgentEvent.INITIALIZE)
                self._fsm.on_event(ResourceAgentEvent.GO_ACTIVE)
                cur_state = self._fsm.get_current_state()
                if cur_state != ResourceAgentState.IDLE:
                    raise Exception()
                self._fsm.on_event(ResourceAgentEvent.RUN)
                cur_state = self._fsm.get_current_state()
                if cur_state != ResourceAgentState.COMMAND:
                    raise Exception()

            # If active unknown, return to active unknown or command if
            # possible. Initialize, activate, confirm active unknown, else
            # confirm idle, run, confirm command.
            elif state == ResourceAgentState.ACTIVE_UNKNOWN:
                self._fsm.on_event(ResourceAgentEvent.INITIALIZE)
                self._fsm.on_event(ResourceAgentEvent.GO_ACTIVE)
                cur_state = self._fsm.get_current_state()
                if cur_state == ResourceAgentState.ACTIVE_UNKNOWN:
                    return
                elif cur_state != ResourceAgentState.IDLE:
                    raise Exception()
                self._fsm.on_event(ResourceAgentEvent.RUN)
                cur_state = self._fsm.get_current_state()
                if cur_state != ResourceAgentState.COMMAND:
                    raise Exception()

            else:
                log.error('Instrument agent %s error restoring unhandled state %s, current state %s.',
                        self.id, state, cur_state)

        except Exception as ex:
            log.error('Instrument agent %s error restoring state %s, current state %s, exception %s.',
                    self.id, state, cur_state, str(ex))
            log.exception('###### Agent restore stack trace:')

        else:
            log.info('Instrument agent %s restored state %s = %s.',
                     self.id, state, cur_state)


class FactorialRetryCalculator(object):
    """
    Object for tracking retry state and calculate retry times based on a factorial sequence
    """
    def __init__(self, retry_coefficient, retry_tolerance_coefficient, maximum_retry_time):
        """
        @param retry_coefficient multiplier for calculating retry time in seconds
        @param retry_tolerance_coefficient multiplier for calculating range for retry expiration
        @param maximum_retry_time max seconds to allow for a retry
        """
        if not retry_coefficient > 0:
            ValueError("retry coefficient must be > 0")

        if not retry_tolerance_coefficient > 1:
            ValueError("retry coefficient must be > 1")

        if not maximum_retry_time > 0:
            ValueError("retry coefficient must be > 0")

        self._retry_coeff = retry_coefficient
        self._tolerance_coeff = retry_tolerance_coefficient
        self._max_retry_time = maximum_retry_time
        self._max_retry_index = None
        self.reset_retry_counter()

    def reset_retry_counter(self):
        """
        Clear all retry state
        """
        self._last_retry = None
        self._retry_count = 0

    def get_sleep_time(self):
        """
        Determine if we need to reset our counters.  Then calculate the next sleep time in the
        sequence and adjust state.
        """
        self._clear_retry_state()

        self._last_retry = time.time()
        self._retry_count += 1

        return self._calc_sleep_time(self._retry_count - 1)

    def _clear_retry_state(self):
        """
        If we need to clear the retry state, do it!
        """
        retry = self._retry_count - 1
        if retry < 0: retry = 0

        time_delta = self._calc_sleep_time(retry) * self._tolerance_coeff
        if self._last_retry is None or  time.time() - self._last_retry > time_delta:
            self.reset_retry_counter()

    def _calc_sleep_time(self, retry_index):
        """
        Calculate the sleep time for a retry index
        """
        # If we have already passed the retry index that would return the max timeout
        # then there is no reason to calculate the timeout.
        if self._max_retry_index is not None and self._max_retry_index <= self._retry_count:
            return self._max_retry_time

        # Calculate the timeout
        else:
            timeout = self._retry_coeff * math.factorial(retry_index)
            if timeout >= self._max_retry_time:
                self._max_retry_index = retry_index + 1
                return self._max_retry_time
            else:
                return timeout
