# Copyright 2016 Red Hat, Inc.
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

import functools

import mock
from neutron_lib.api.definitions import portbindings
from oslo_config import cfg
from oslo_utils import uuidutils

from neutron.common.ovn import constants as ovn_const
from neutron.common.ovn import utils
from neutron.common import utils as n_utils
from neutron.db import ovn_revision_numbers_db as db_rev
from neutron.tests import base as tests_base
from neutron.tests.functional import base


class TestPortBinding(base.TestOVNFunctionalBase):

    def setUp(self):
        super(TestPortBinding, self).setUp()
        self.ovs_host = 'ovs-host'
        self.dpdk_host = 'dpdk-host'
        self.invalid_dpdk_host = 'invalid-host'
        self.vhu_mode = 'server'
        self.add_fake_chassis(self.ovs_host)
        self.add_fake_chassis(
            self.dpdk_host,
            external_ids={'datapath-type': 'netdev',
                          'iface-types': 'dummy,dummy-internal,dpdkvhostuser'})

        self.add_fake_chassis(
            self.invalid_dpdk_host,
            external_ids={'datapath-type': 'netdev',
                          'iface-types': 'dummy,dummy-internal,geneve,vxlan'})
        self.n1 = self._make_network(self.fmt, 'n1', True)
        res = self._create_subnet(self.fmt, self.n1['network']['id'],
                                  '10.0.0.0/24')
        self.deserialize(self.fmt, res)

    def _create_or_update_port(self, port_id=None, hostname=None):

        if port_id is None:
            port_data = {
                'port': {'network_id': self.n1['network']['id'],
                         'tenant_id': self._tenant_id}}

            if hostname:
                port_data['port']['device_id'] = uuidutils.generate_uuid()
                port_data['port']['device_owner'] = 'compute:None'
                port_data['port']['binding:host_id'] = hostname

            port_req = self.new_create_request('ports', port_data, self.fmt)
            port_res = port_req.get_response(self.api)
            p = self.deserialize(self.fmt, port_res)
            port_id = p['port']['id']
        else:
            port_data = {
                'port': {'device_id': uuidutils.generate_uuid(),
                         'device_owner': 'compute:None',
                         'binding:host_id': hostname}}
            port_req = self.new_update_request('ports', port_data, port_id,
                                               self.fmt)
            port_res = port_req.get_response(self.api)
            self.deserialize(self.fmt, port_res)

        return port_id

    def _verify_vif_details(self, port_id, expected_host_name,
                            expected_vif_type, expected_vif_details):
        port_req = self.new_show_request('ports', port_id)
        port_res = port_req.get_response(self.api)
        p = self.deserialize(self.fmt, port_res)
        self.assertEqual(expected_host_name, p['port']['binding:host_id'])
        self.assertEqual(expected_vif_type, p['port']['binding:vif_type'])
        self.assertEqual(expected_vif_details,
                         p['port']['binding:vif_details'])

    def test_port_binding_create_port(self):
        port_id = self._create_or_update_port(hostname=self.ovs_host)
        self._verify_vif_details(port_id, self.ovs_host, 'ovs',
                                 {'port_filter': True})

        port_id = self._create_or_update_port(hostname=self.dpdk_host)
        expected_vif_details = {'port_filter': False,
                                'vhostuser_mode': self.vhu_mode,
                                'vhostuser_ovs_plug': True}
        expected_vif_details['vhostuser_socket'] = (
            utils.ovn_vhu_sockpath(cfg.CONF.ovn.vhost_sock_dir, port_id))
        self._verify_vif_details(port_id, self.dpdk_host, 'vhostuser',
                                 expected_vif_details)

        port_id = self._create_or_update_port(hostname=self.invalid_dpdk_host)
        self._verify_vif_details(port_id, self.invalid_dpdk_host, 'ovs',
                                 {'port_filter': True})

    def test_port_binding_update_port(self):
        port_id = self._create_or_update_port()
        self._verify_vif_details(port_id, '', 'unbound', {})
        port_id = self._create_or_update_port(port_id=port_id,
                                              hostname=self.ovs_host)
        self._verify_vif_details(port_id, self.ovs_host, 'ovs',
                                 {'port_filter': True})

        port_id = self._create_or_update_port(port_id=port_id,
                                              hostname=self.dpdk_host)
        expected_vif_details = {'port_filter': False,
                                'vhostuser_mode': self.vhu_mode,
                                'vhostuser_ovs_plug': True}
        expected_vif_details['vhostuser_socket'] = (
            utils.ovn_vhu_sockpath(cfg.CONF.ovn.vhost_sock_dir, port_id))
        self._verify_vif_details(port_id, self.dpdk_host, 'vhostuser',
                                 expected_vif_details)

        port_id = self._create_or_update_port(port_id=port_id,
                                              hostname=self.invalid_dpdk_host)
        self._verify_vif_details(port_id, self.invalid_dpdk_host, 'ovs',
                                 {'port_filter': True})


class TestPortBindingOverTcp(TestPortBinding):
    def get_ovsdb_server_protocol(self):
        return 'tcp'


# TODO(mjozefcz): This test class hangs during execution.
class TestPortBindingOverSsl(TestPortBinding):
    def get_ovsdb_server_protocol(self):
        return 'ssl'


class TestNetworkMTUUpdate(base.TestOVNFunctionalBase):

    def setUp(self):
        super(TestNetworkMTUUpdate, self).setUp()
        self._ovn_client = self.mech_driver._ovn_client
        self.n1 = self._make_network(self.fmt, 'n1', True)
        res = self._create_subnet(self.fmt, self.n1['network']['id'],
                                  '10.0.0.0/24')
        self.sub = self.deserialize(self.fmt, res)

    def test_update_network_mtu(self):
        mtu_value = self.n1['network']['mtu'] - 100
        dhcp_options = (
            self.mech_driver._ovn_client._nb_idl.get_subnet_dhcp_options(
                self.sub['subnet']['id'])
        )
        self.assertNotEqual(
            int(dhcp_options['subnet']['options']['mtu']),
            mtu_value)
        data = {'network': {'mtu': mtu_value}}
        req = self.new_update_request(
            'networks', data, self.n1['network']['id'], self.fmt)
        req.get_response(self.api)
        dhcp_options = (
            self.mech_driver._ovn_client._nb_idl.get_subnet_dhcp_options(
                self.sub['subnet']['id'])
        )
        self.assertEqual(
            int(dhcp_options['subnet']['options']['mtu']),
            mtu_value)

    def test_no_update_network_mtu(self):
        mtu_value = self.n1['network']['mtu']
        base_revision = db_rev.get_revision_row(
            self.context,
            self.sub['subnet']['id'])
        data = {'network': {'mtu': mtu_value}}
        req = self.new_update_request(
            'networks', data, self.n1['network']['id'], self.fmt)
        req.get_response(self.api)
        second_revision = db_rev.get_revision_row(
            self.context,
            self.sub['subnet']['id'])
        self.assertEqual(
            base_revision.updated_at,
            second_revision.updated_at)


@mock.patch('neutron.plugins.ml2.drivers.ovn.mech_driver.'
            'ovsdb.ovn_client.OVNClient._is_virtual_port_supported',
            lambda *args: True)
class TestVirtualPorts(base.TestOVNFunctionalBase):

    def setUp(self):
        super(TestVirtualPorts, self).setUp()
        self._ovn_client = self.mech_driver._ovn_client
        self.n1 = self._make_network(self.fmt, 'n1', True)
        res = self._create_subnet(self.fmt, self.n1['network']['id'],
                                  '10.0.0.0/24')
        self.sub = self.deserialize(self.fmt, res)

    def _create_port(self, fixed_ip=None, allowed_address=None):
        port_data = {
            'port': {'network_id': self.n1['network']['id'],
                     'tenant_id': self._tenant_id}}
        if fixed_ip:
            port_data['port']['fixed_ips'] = [{'ip_address': fixed_ip}]

        if allowed_address:
            port_data['port']['allowed_address_pairs'] = [
                {'ip_address': allowed_address}]

        port_req = self.new_create_request('ports', port_data, self.fmt)
        port_res = port_req.get_response(self.api)
        self.assertEqual(201, port_res.status_int)
        return self.deserialize(self.fmt, port_res)['port']

    def _update_allowed_address_pair(self, port_id, data):
        port_data = {
            'port': {'allowed_address_pairs': data}}
        port_req = self.new_update_request('ports', port_data, port_id,
                                           self.fmt)
        port_res = port_req.get_response(self.api)
        self.assertEqual(200, port_res.status_int)
        return self.deserialize(self.fmt, port_res)['port']

    def _set_allowed_address_pair(self, port_id, ip):
        return self._update_allowed_address_pair(port_id, [{'ip_address': ip}])

    def _unset_allowed_address_pair(self, port_id):
        return self._update_allowed_address_pair(port_id, [])

    def _find_port_row(self, port_id):
        cmd = self.nb_api.db_find_rows(
            'Logical_Switch_Port', ('name', '=', port_id))
        rows = cmd.execute(check_error=True)
        return rows[0] if rows else None

    def _is_ovn_port_type(self, port_id, port_type):
        ovn_vport = self._find_port_row(port_id)
        return port_type == ovn_vport.type

    def _check_port_type(self, port_id, type):
        check = functools.partial(self._is_ovn_port_type, port_id, type)
        n_utils.wait_until_true(check, timeout=10)

    @tests_base.unstable_test("bug 1865453")
    def test_virtual_port_created_before(self):
        virt_port = self._create_port()
        virt_ip = virt_port['fixed_ips'][0]['ip_address']

        # Create the master port with the VIP address already set in
        # the allowed_address_pairs field
        master = self._create_port(allowed_address=virt_ip)

        # Assert the virt port has the type virtual and master is set
        # as parent
        self._check_port_type(virt_port['id'], ovn_const.LSP_TYPE_VIRTUAL)
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertEqual(
            master['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

        # Create the backport parent port
        backup = self._create_port(allowed_address=virt_ip)

        # Assert the virt port now also includes the backup port as a parent
        self._check_port_type(virt_port['id'], ovn_const.LSP_TYPE_VIRTUAL)
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertIn(
            master['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])
        self.assertIn(
            backup['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

    @tests_base.unstable_test("bug 1865453")
    def test_virtual_port_update_address_pairs(self):
        master = self._create_port()
        backup = self._create_port()
        virt_port = self._create_port()
        virt_ip = virt_port['fixed_ips'][0]['ip_address']

        # Assert the virt port does not yet have the type virtual (no
        # address pairs were set yet)
        self._check_port_type(virt_port['id'], ''),
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY,
                         ovn_vport.options)
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY,
                         ovn_vport.options)

        # Set the virt IP to the allowed address pairs of the master port
        self._set_allowed_address_pair(master['id'], virt_ip)

        # Assert the virt port is now updated
        self._check_port_type(virt_port['id'], ovn_const.LSP_TYPE_VIRTUAL),
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertEqual(
            master['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

        # Set the virt IP to the allowed address pairs of the backup port
        self._set_allowed_address_pair(backup['id'], virt_ip)

        # Assert the virt port now includes the backup port as a parent
        self._check_port_type(virt_port['id'], ovn_const.LSP_TYPE_VIRTUAL),
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertIn(
            master['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])
        self.assertIn(
            backup['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

        # Remove the address pairs from the master port
        self._unset_allowed_address_pair(master['id'])

        # Assert the virt port now only has the backup port as a parent
        self._check_port_type(virt_port['id'], ovn_const.LSP_TYPE_VIRTUAL),
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertEqual(
            backup['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

        # Remove the address pairs from the backup port
        self._unset_allowed_address_pair(backup['id'])

        # Assert the virt port is not type virtual anymore and the virtual
        # port options are cleared
        self._check_port_type(virt_port['id'], ''),
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY,
                         ovn_vport.options)
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY,
                         ovn_vport.options)

    @tests_base.unstable_test("bug 1865453")
    def test_virtual_port_created_after(self):
        master = self._create_port(fixed_ip='10.0.0.11')
        backup = self._create_port(fixed_ip='10.0.0.12')
        virt_ip = '10.0.0.55'

        # Set the virt IP to the master and backup ports *before* creating
        # the virtual port
        self._set_allowed_address_pair(master['id'], virt_ip)
        self._set_allowed_address_pair(backup['id'], virt_ip)

        virt_port = self._create_port(fixed_ip=virt_ip)

        # Assert the virtual port has been created with the
        # right type and parents
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(ovn_const.LSP_TYPE_VIRTUAL, ovn_vport.type)
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertIn(
            master['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])
        self.assertIn(
            backup['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

    @tests_base.unstable_test("bug 1865453")
    def test_virtual_port_delete_parents(self):
        master = self._create_port()
        backup = self._create_port()
        virt_port = self._create_port()
        virt_ip = virt_port['fixed_ips'][0]['ip_address']

        # Assert the virt port does not yet have the type virtual (no
        # address pairs were set yet)
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual("", ovn_vport.type)
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY,
                         ovn_vport.options)
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY,
                         ovn_vport.options)

        # Set allowed address paris to the master and backup ports
        self._set_allowed_address_pair(master['id'], virt_ip)
        self._set_allowed_address_pair(backup['id'], virt_ip)

        # Assert the virtual port is correct
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(ovn_const.LSP_TYPE_VIRTUAL, ovn_vport.type)
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertIn(
            master['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])
        self.assertIn(
            backup['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

        # Delete the backup port
        self._delete('ports', backup['id'])

        # Assert the virt port now only has the master port as a parent
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual(ovn_const.LSP_TYPE_VIRTUAL, ovn_vport.type)
        self.assertEqual(
            virt_ip,
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY])
        self.assertEqual(
            master['id'],
            ovn_vport.options[ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY])

        # Delete the master port
        self._delete('ports', master['id'])

        # Assert the virt port is not type virtual anymore and the virtual
        # port options are cleared
        ovn_vport = self._find_port_row(virt_port['id'])
        self.assertEqual("", ovn_vport.type)
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_PARENTS_KEY,
                         ovn_vport.options)
        self.assertNotIn(ovn_const.LSP_OPTIONS_VIRTUAL_IP_KEY,
                         ovn_vport.options)


class TestExternalPorts(base.TestOVNFunctionalBase):

    def setUp(self):
        super(TestExternalPorts, self).setUp()
        self._ovn_client = self.mech_driver._ovn_client
        self.n1 = self._make_network(self.fmt, 'n1', True)
        res = self._create_subnet(self.fmt, self.n1['network']['id'],
                                  '10.0.0.0/24')
        self.sub = self.deserialize(self.fmt, res)

        # The default group will be created by the maintenance task (
        # which is disabled in the functional jobs). So let's add it
        self.default_ch_grp = self.nb_api.ha_chassis_group_add(
            ovn_const.HA_CHASSIS_GROUP_DEFAULT_NAME).execute(check_error=True)

    def _find_port_row_by_name(self, name):
        cmd = self.nb_api.db_find_rows(
            'Logical_Switch_Port', ('name', '=', name))
        rows = cmd.execute(check_error=True)
        return rows[0] if rows else None

    def test_external_port_create(self):
        port_data = {
            'port': {'network_id': self.n1['network']['id'],
                     'tenant_id': self._tenant_id,
                     portbindings.VNIC_TYPE: portbindings.VNIC_DIRECT}}

        port_req = self.new_create_request('ports', port_data, self.fmt)
        port_res = port_req.get_response(self.api)
        port = self.deserialize(self.fmt, port_res)['port']

        ovn_port = self._find_port_row_by_name(port['id'])
        self.assertEqual(ovn_const.LSP_TYPE_EXTERNAL, ovn_port.type)
        self.assertEqual(1, len(ovn_port.ha_chassis_group))
        self.assertEqual(str(self.default_ch_grp.uuid),
                         str(ovn_port.ha_chassis_group[0].uuid))

    def test_external_port_update(self):
        port_data = {
            'port': {'network_id': self.n1['network']['id'],
                     'tenant_id': self._tenant_id}}

        port_req = self.new_create_request('ports', port_data, self.fmt)
        port_res = port_req.get_response(self.api)
        port = self.deserialize(self.fmt, port_res)['port']

        ovn_port = self._find_port_row_by_name(port['id'])
        self.assertEqual('', ovn_port.type)
        self.assertEqual([], ovn_port.ha_chassis_group)

        port_upt_data = {
            'port': {portbindings.VNIC_TYPE: portbindings.VNIC_DIRECT}}
        port_req = self.new_update_request(
            'ports', port_upt_data, port['id'], self.fmt)
        port_res = port_req.get_response(self.api)
        port = self.deserialize(self.fmt, port_res)['port']

        ovn_port = self._find_port_row_by_name(port['id'])
        self.assertEqual(ovn_const.LSP_TYPE_EXTERNAL, ovn_port.type)
        self.assertEqual(1, len(ovn_port.ha_chassis_group))
        self.assertEqual(str(self.default_ch_grp.uuid),
                         str(ovn_port.ha_chassis_group[0].uuid))

    def test_external_port_create_switchdev(self):
        port_data = {
            'port': {'network_id': self.n1['network']['id'],
                     'tenant_id': self._tenant_id,
                     portbindings.VNIC_TYPE: portbindings.VNIC_DIRECT,
                     ovn_const.OVN_PORT_BINDING_PROFILE: {
                     'capabilities': [ovn_const.PORT_CAP_SWITCHDEV]}}}

        port_req = self.new_create_request('ports', port_data, self.fmt)
        port_res = port_req.get_response(self.api)
        port = self.deserialize(self.fmt, port_res)['port']

        ovn_port = self._find_port_row_by_name(port['id'])
        # When "switchdev" is set, we should treat it as a normal
        # port instead of "external" type
        self.assertEqual("", ovn_port.type)
        # Assert the poer hasn't been added to any HA Chassis Group either
        self.assertEqual(0, len(ovn_port.ha_chassis_group))

    def test_external_port_update_switchdev(self):
        port_data = {
            'port': {'network_id': self.n1['network']['id'],
                     'tenant_id': self._tenant_id,
                     portbindings.VNIC_TYPE: portbindings.VNIC_DIRECT}}

        # Create a VNIC_DIRECT type port without the "switchdev"
        # capability and assert that it's an "external" port
        port_req = self.new_create_request('ports', port_data, self.fmt)
        port_res = port_req.get_response(self.api)
        port = self.deserialize(self.fmt, port_res)['port']

        ovn_port = self._find_port_row_by_name(port['id'])
        self.assertEqual(ovn_const.LSP_TYPE_EXTERNAL, ovn_port.type)
        self.assertEqual(1, len(ovn_port.ha_chassis_group))
        self.assertEqual(str(self.default_ch_grp.uuid),
                         str(ovn_port.ha_chassis_group[0].uuid))

        # Now, update the port to add a "switchdev" capability and make
        # sure it's not treated as an "external" port anymore nor it's
        # included in a HA Chassis Group
        port_upt_data = {
            'port': {ovn_const.OVN_PORT_BINDING_PROFILE: {
                     'capabilities': [ovn_const.PORT_CAP_SWITCHDEV]}}}
        port_req = self.new_update_request(
            'ports', port_upt_data, port['id'], self.fmt)
        port_res = port_req.get_response(self.api)
        port = self.deserialize(self.fmt, port_res)['port']

        ovn_port = self._find_port_row_by_name(port['id'])
        # When "switchdev" is set, we should treat it as a normal
        # port instead of "external" type
        self.assertEqual("", ovn_port.type)
        # Assert the poer hasn't been added to any HA Chassis Group either
        self.assertEqual(0, len(ovn_port.ha_chassis_group))
