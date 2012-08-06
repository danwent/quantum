"""
# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2012 Nicira Networks, Inc.  All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Dan Wendlandt, Nicira, Inc
#
"""

from abc import abstractmethod
import logging
import webob

from quantum.api.v2 import router as v2_api_router
from quantum.common import exceptions as qexception
from quantum.extensions import extensions
from quantum.openstack.common import cfg


LOG = logging.getLogger("quantum.extensions.L3Nat")


# Exceptions

class RouterNotFound(qexception.NotFound):
    message = _("Router %(router_id)s could not be found")

class RouterInUse(qexception.InUse):
    message = _("Router %(router_id)s still has active ports")

class FloatingIPNotFound(qexception.NotFound):
    message = _("Floating IP %(floatingip_id)s could not be found")


class L3_nat(object):

    @classmethod
    def get_name(cls):
        return "Quantum Router"

    @classmethod
    def get_alias(cls):
        return "os-quantum-router"

    @classmethod
    def get_description(cls):
        return ("Router abstraction for basic L3 + NAT forwarding"
               " between L2 Quantum networks")

    @classmethod
    def get_namespace(cls):
        return "http://docs.openstack.org/ext/os-quantum-router/api/v1.0"

    @classmethod
    def get_updated(cls):
        return "2012-07-20T10:00:00-00:00"

    def get_added_resources(self, version):
        if version == "2.0":
            return [('router', {'collection_name': 'routers'}),
                    ('floatingip', {'collection_name': 'floatingips'})]

class RouterPluginBase(object):

    @abstractmethod
    def create_router(self, context, router):
        pass

    @abstractmethod
    def update_router(self, context, id, router):
        pass

    @abstractmethod
    def get_router(self, context, id, fields=None, verbose=None):
        pass

    @abstractmethod
    def delete_router(self, context, id):
        pass

    @abstractmethod
    def get_routers(self, context, filters=None, fields=None, verbose=None):
        pass

    @abstractmethod
    def add_router_interface(self, context, router_id, interface_info):
        pass

    @abstractmethod
    def create_floatingip(self, context, floatingip):
        pass

    @abstractmethod
    def update_floatingip(self, context, id, floatingip):
        pass

    @abstractmethod
    def get_floatingip(self, context, id, fields=None, verbose=None):
        pass

    @abstractmethod
    def delete_floatingip(self, context, id):
        pass

    @abstractmethod
    def get_floatingips(self, context, filters=None, fields=None, verbose=None):
        pass
