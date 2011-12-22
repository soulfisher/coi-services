#!/usr/bin/env python


__author__ = 'Stephen P. Henrie'
__license__ = 'Apache 2.0'

from interface.services.coi.ipolicy_management_service import BasePolicyManagementService
from pyon.core.exception import Conflict, Inconsistent, NotFound
from pyon.public import AT, RT
from pyon.util.log import log

class PolicyManagementService(BasePolicyManagementService):

    def create_policy(self, policy={}, org_id=''):
        """Persists the provided Policy object for the specified Org id. The id string returned
        is the internal id by which Policy will be indentified in the data store.

        @param policy    Policy
        @param org_id    str
        @retval policy_id    str
        @throws BadRequest    if object passed has _id or _rev attribute
        """
        policy_id, version = self.clients.resource_registry.create(policy)
        return policy_id

    def update_policy(self, policy={}):
        """Updates the provided Policy object.  Throws NotFound exception if
        an existing version of Policy is not found.  Throws Conflict if
        the provided Policy object is not based on the latest persisted
        version of the object.

        @param policy    Policy
        @throws NotFound    object with specified id does not exist
        @throws BadRequest    if object does not have _id or _rev attribute
        @throws Conflict    object not based on latest persisted object version
        """
        self.clients.resource_registry.update(policy)

    def read_policy(self, policy_id=''):
        """Returns the Policy object for the specified policy id.
        Throws exception if id does not match any persisted Policy
        objects.

        @param policy_id    str
        @retval policy    Policy
        @throws NotFound    object with specified id does not exist
        """
        policy = self.clients.resource_registry.read(policy_id)
        if not policy:
            raise NotFound("Policy %s does not exist" % policy_id)
        return policy

    def delete_policy(self, policy_id=''):
        """For now, permanently deletes Policy object with the specified
        id. Throws exception if id does not match any persisted Policy.

        @param policy_id    str
        @throws NotFound    object with specified id does not exist
        """
        policy = self.clients.resource_registry.read(policy_id)
        if not policy:
            raise NotFound("Policy %s does not exist" % policy_id)
        self.clients.resource_registry.delete(policy)

    def enable_policy(self, policy_id=''):
        """Advances the lifecycle state of the specified Policy object to be enabled. Only
        enabled policies should be considered by the policy engine.

        @param policy_id    str
        @throws NotFound    object with specified id does not exist
        """
        pass


    def disable_policy(self, policy_id=''):
        """Advances the lifecycle state of the specified Policy object to be disabled. Only
        enabled policies should be considered by the policy engine.

        @param policy_id    str
        @throws NotFound    object with specified id does not exist
        """
        pass

    def create_role(self, role={}):
        """Persists the provided UserRole object. The id string returned
        is the internal id by which a UserRole will be indentified in the data store.

        @param role    UserRole
        @retval role_id    str
        @throws BadRequest    if object passed has _id or _rev attribute
        """
        role_id, version = self.clients.resource_registry.create(role)
        return role_id

    def update_role(self, role={}):
        """Updates the provided UserRole object.  Throws NotFound exception if
        an existing version of UserRole is not found.  Throws Conflict if
        the provided UserRole object is not based on the latest persisted
        version of the object.

        @param role    UserRole
        @retval success    bool
        @throws NotFound    object with specified id does not exist
        @throws BadRequest    if object does not have _id or _rev attribute
        @throws Conflict    object not based on latest persisted object version
        """
        self.clients.resource_registry.update(role)

    def read_role(self, role_id=''):
        """Returns the UserRole object for the specified role id.
        Throws exception if id does not match any persisted UserRole
        objects.

        @param role_id    str
        @retval role    UserRole
        @throws NotFound    object with specified id does not exist
        """
        role = self.clients.resource_registry.read(role_id)
        if not role:
            raise NotFound("Role %s does not exist" % role_id)
        return role

    def delete_role(self, role_id=''):
        """For now, permanently deletes UserRole object with the specified
        id. Throws exception if id does not match any persisted UserRole.

        @param role_id    str
        @throws NotFound    object with specified id does not exist
        """
        role = self.clients.resource_registry.read(role_id)
        if not role:
            raise NotFound("Role %s does not exist" % role_id)
        self.clients.resource_registry.delete(role)


    def grant_role(self, org_id='', role_id='', user_id='', scope={}):
        """Grants a defined role within an organization to a specific user. Will throw a not NotFound exception
        if none of the specified ids do not exist.

        @param org_id    str
        @param role_id    str
        @param user_id    str
        @param scope    RoleScope
        @throws NotFound    object with specified id does not exist
        """
        pass

    def revoke_role(self, org_id='', user_id='', role_id=''):
        """Revokes a defined role within an organization to a specific user. Will throw a not NotFound exception
        if none of the specified ids do not exist.

        @param org_id    str
        @param user_id    str
        @param role_id    str
        @throws NotFound    object with specified id does not exist
        """
        pass

    def find_roles_by_user(self, org_id='', user_id=''):
        """Returns a list of orgaization roles for a specific user. Will throw a not NotFound exception
        if none of the specified ids do not exist.

        @param org_id    str
        @param user_id    str
        @retval role_list    []
        @throws NotFound    object with specified id does not exist
        """
        pass


    def has_permission(self, org_id='', action_id='', user_id='', resource_id=''):
        """Returns a boolean of the specified user has permission for the specified action on a specified resource. Will
        throw a NotFound exception if none of the specified ids do not exist.

        @param org_id    str
        @param action_id    str
        @param user_id    str
        @param resource_id    str
        @retval has_permission    bool
        @throws NotFound    object with specified id does not exist
        """
        pass

