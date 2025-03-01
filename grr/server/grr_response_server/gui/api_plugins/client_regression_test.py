#!/usr/bin/env python
"""This modules contains regression tests for clients API handlers."""
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from absl import app
from future.builtins import range
from future.builtins import str

from grr_response_core.lib import rdfvalue
from grr_response_core.lib.rdfvalues import client as rdf_client
from grr_response_core.lib.rdfvalues import client_network as rdf_client_network
from grr_response_core.lib.rdfvalues import client_stats as rdf_client_stats
from grr_response_server import data_store
from grr_response_server import flow
from grr_response_server.flows.general import processes

from grr_response_server.gui import api_regression_test_lib
from grr_response_server.gui.api_plugins import client as client_plugin
from grr_response_server.rdfvalues import flow_objects as rdf_flow_objects
from grr.test_lib import flow_test_lib
from grr.test_lib import hunt_test_lib
from grr.test_lib import test_lib


class ApiSearchClientsHandlerRegressionTest(
    api_regression_test_lib.ApiRegressionTest):

  api_method = "SearchClients"
  handler = client_plugin.ApiSearchClientsHandler

  def Run(self):
    # Fix the time to avoid regressions.
    with test_lib.FakeTime(42):
      client_id = self.SetupClient(0)

      self.Check(
          "SearchClients",
          args=client_plugin.ApiSearchClientsArgs(query=client_id))


class ApiGetClientHandlerRegressionTest(
    api_regression_test_lib.ApiRegressionTest):

  api_method = "GetClient"
  handler = client_plugin.ApiGetClientHandler

  def Run(self):
    # Fix the time to avoid regressions.
    with test_lib.FakeTime(42):
      client_id = self.SetupClient(0, memory_size=4294967296, add_cert=False)

    self.Check(
        "GetClient", args=client_plugin.ApiGetClientArgs(client_id=client_id))


class ApiGetClientVersionsRegressionTest(
    api_regression_test_lib.ApiRegressionTest):

  mode = "FULL"

  api_method = "GetClientVersions"
  handler = client_plugin.ApiGetClientVersionsHandler

  def _SetupTestClient(self):
    with test_lib.FakeTime(42):
      client_id = self.SetupClient(0, memory_size=4294967296, add_cert=False)

    with test_lib.FakeTime(45):
      self.SetupClient(
          0,
          fqdn="some-other-hostname.org",
          memory_size=4294967296,
          add_cert=False)

    return client_id

  def Run(self):
    client_id = self._SetupTestClient()

    with test_lib.FakeTime(47):
      self.Check(
          "GetClientVersions",
          args=client_plugin.ApiGetClientVersionsArgs(
              client_id=client_id, mode=self.mode))
      self.Check(
          "GetClientVersions",
          args=client_plugin.ApiGetClientVersionsArgs(
              client_id=client_id,
              end=rdfvalue.RDFDatetime.FromSecondsSinceEpoch(44),
              mode=self.mode))
      self.Check(
          "GetClientVersions",
          args=client_plugin.ApiGetClientVersionsArgs(
              client_id=client_id,
              start=rdfvalue.RDFDatetime.FromSecondsSinceEpoch(44),
              end=rdfvalue.RDFDatetime.FromSecondsSinceEpoch(46),
              mode=self.mode))


class ApiGetLastClientIPAddressHandlerRegressionTest(
    api_regression_test_lib.ApiRegressionTest):

  api_method = "GetLastClientIPAddress"
  handler = client_plugin.ApiGetLastClientIPAddressHandler

  def Run(self):
    # Fix the time to avoid regressions.
    with test_lib.FakeTime(42):
      client_id = self.SetupClient(0)

      ip = rdf_client_network.NetworkAddress(
          human_readable_address="192.168.100.42",
          address_type=rdf_client_network.NetworkAddress.Family.INET)
      data_store.REL_DB.WriteClientMetadata(client_id, last_ip=ip)

    self.Check(
        "GetLastClientIPAddress",
        args=client_plugin.ApiGetLastClientIPAddressArgs(client_id=client_id))


class ApiListClientsLabelsHandlerRegressionTest(
    api_regression_test_lib.ApiRegressionTest):

  api_method = "ListClientsLabels"
  handler = client_plugin.ApiListClientsLabelsHandler

  def Run(self):
    # Fix the time to avoid regressions.
    with test_lib.FakeTime(42):
      client_ids = self.SetupClients(2)

      self.AddClientLabel(client_ids[0], self.token.username, u"foo")
      self.AddClientLabel(client_ids[0], self.token.username, u"bar")

    self.Check("ListClientsLabels")


class ApiListKbFieldsHandlerTest(api_regression_test_lib.ApiRegressionTest):

  api_method = "ListKbFields"
  handler = client_plugin.ApiListKbFieldsHandler

  def Run(self):
    self.Check("ListKbFields")


class ApiListClientCrashesHandlerRegressionTest(
    api_regression_test_lib.ApiRegressionTest,
    hunt_test_lib.StandardHuntTestMixin):

  api_method = "ListClientCrashes"
  handler = client_plugin.ApiListClientCrashesHandler

  def Run(self):
    client_id = self.SetupClient(0)

    client_mock = flow_test_lib.CrashClientMock(client_id, self.token)

    with test_lib.FakeTime(42):
      hunt_id = self.StartHunt(description="the hunt")

    with test_lib.FakeTime(45):
      self.AssignTasksToClients([client_id])
      hunt_test_lib.TestHuntHelperWithMultipleMocks({client_id: client_mock},
                                                    self.token)

    crashes = data_store.REL_DB.ReadClientCrashInfoHistory(str(client_id))
    crash = list(crashes)[0]
    replace = {hunt_id: "H:123456", str(crash.session_id): "<some session id>"}

    self.Check(
        "ListClientCrashes",
        args=client_plugin.ApiListClientCrashesArgs(client_id=client_id),
        replace=replace)
    self.Check(
        "ListClientCrashes",
        args=client_plugin.ApiListClientCrashesArgs(
            client_id=client_id, count=1),
        replace=replace)
    self.Check(
        "ListClientCrashes",
        args=client_plugin.ApiListClientCrashesArgs(
            client_id=client_id, offset=1, count=1),
        replace=replace)


class ApiListClientActionRequestsHandlerRegressionTest(
    api_regression_test_lib.ApiRegressionTest,
    hunt_test_lib.StandardHuntTestMixin):

  api_method = "ListClientActionRequests"
  handler = client_plugin.ApiListClientActionRequestsHandler

  def _StartFlow(self, client_id, flow_cls, **kw):
    flow_id = flow.StartFlow(flow_cls=flow_cls, client_id=client_id, **kw)
    # Lease the client message.
    data_store.REL_DB.LeaseClientActionRequests(
        client_id, lease_time=rdfvalue.DurationSeconds("10000s"))
    # Write some responses. In the relational db, the client queue will be
    # cleaned up as soon as all responses are available. Therefore we cheat
    # here and make it look like the request needs more responses so it's not
    # considered complete.

    # Write the status first. This will mark the request as waiting for 2
    # responses.
    status = rdf_flow_objects.FlowStatus(
        client_id=client_id, flow_id=flow_id, request_id=1, response_id=2)
    data_store.REL_DB.WriteFlowResponses([status])

    # Now we read the request, adjust the number, and write it back.
    reqs = data_store.REL_DB.ReadAllFlowRequestsAndResponses(client_id, flow_id)
    req = reqs[0][0]

    req.nr_responses_expected = 99

    data_store.REL_DB.WriteFlowRequests([req])

    # This response now won't trigger any deletion of client messages.
    response = rdf_flow_objects.FlowResponse(
        client_id=client_id,
        flow_id=flow_id,
        request_id=1,
        response_id=1,
        payload=rdf_client.Process(name="test_process"))
    data_store.REL_DB.WriteFlowResponses([response])

    # This is not strictly needed as we don't display this information in the
    # UI.
    req.nr_responses_expected = 2
    data_store.REL_DB.WriteFlowRequests([req])

    return flow_id

  def Run(self):
    client_id = self.SetupClient(0)

    with test_lib.FakeTime(42):
      flow_id = self._StartFlow(client_id, processes.ListProcesses)

    replace = api_regression_test_lib.GetFlowTestReplaceDict(client_id, flow_id)

    self.Check(
        "ListClientActionRequests",
        args=client_plugin.ApiListClientActionRequestsArgs(client_id=client_id),
        replace=replace)
    self.Check(
        "ListClientActionRequests",
        args=client_plugin.ApiListClientActionRequestsArgs(
            client_id=client_id, fetch_responses=True),
        replace=replace)


class ApiGetClientLoadStatsHandlerRegressionTest(
    api_regression_test_lib.ApiRegressionTest):

  api_method = "GetClientLoadStats"
  handler = client_plugin.ApiGetClientLoadStatsHandler

  def FillClientStats(self, client_id):
    stats = []
    for i in range(6):
      timestamp = int((i + 1) * 10 * 1e6)
      st = rdf_client_stats.ClientStats()

      sample = rdf_client_stats.CpuSample(
          timestamp=timestamp,
          user_cpu_time=10 + i,
          system_cpu_time=20 + i,
          cpu_percent=10 + i)
      st.cpu_samples.Append(sample)

      sample = rdf_client_stats.IOSample(
          timestamp=timestamp, read_bytes=10 + i, write_bytes=10 + i * 2)
      st.io_samples.Append(sample)

      stats.append(st)

    for st in stats:
      with test_lib.FakeTime(st.cpu_samples[0].timestamp):
        data_store.REL_DB.WriteClientStats(client_id=client_id, stats=st)

  def Run(self):
    client_id = self.SetupClient(0)
    self.FillClientStats(client_id)

    self.Check(
        "GetClientLoadStats",
        args=client_plugin.ApiGetClientLoadStatsArgs(
            client_id=client_id,
            metric="CPU_PERCENT",
            start=rdfvalue.RDFDatetime.FromSecondsSinceEpoch(10),
            end=rdfvalue.RDFDatetime.FromSecondsSinceEpoch(21)))
    self.Check(
        "GetClientLoadStats",
        args=client_plugin.ApiGetClientLoadStatsArgs(
            client_id=client_id,
            metric="IO_WRITE_BYTES",
            start=rdfvalue.RDFDatetime.FromSecondsSinceEpoch(10),
            end=rdfvalue.RDFDatetime.FromSecondsSinceEpoch(21)))


def main(argv):
  api_regression_test_lib.main(argv)


if __name__ == "__main__":
  app.run(main)
