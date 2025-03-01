#!/usr/bin/env python
"""FS GRR server side integration utility functions."""
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import binascii

from fleetspeak.src.common.proto.fleetspeak import common_pb2 as fs_common_pb2
from fleetspeak.src.server.proto.fleetspeak_server import admin_pb2

from grr_response_core import config
from grr_response_core.lib import rdfvalue
from grr_response_server import data_store
from grr_response_server import fleetspeak_connector


def IsFleetspeakEnabledClient(grr_id):
  """Returns whether the provided GRR id is a Fleetspeak client."""
  if grr_id is None:
    return False

  md = data_store.REL_DB.ReadClientMetadata(grr_id)
  if not md:
    return False
  return md.fleetspeak_enabled


def SendGrrMessageThroughFleetspeak(grr_id, msg):
  """Sends the given GrrMessage through FS."""
  fs_msg = fs_common_pb2.Message(
      message_type="GrrMessage",
      destination=fs_common_pb2.Address(
          client_id=GRRIDToFleetspeakID(grr_id), service_name="GRR"))
  fs_msg.data.Pack(msg.AsPrimitiveProto())
  fleetspeak_connector.CONN.outgoing.InsertMessage(fs_msg)


def FleetspeakIDToGRRID(fs_id):
  return "C." + binascii.hexlify(fs_id).decode("ascii")


def GRRIDToFleetspeakID(grr_id):
  # Strip the 'C.' prefix and convert to binary.
  return binascii.unhexlify(grr_id[2:])


def TSToRDFDatetime(ts):
  """Convert a protobuf.Timestamp to an RDFDatetime."""
  return rdfvalue.RDFDatetime(ts.seconds * 1000000 + ts.nanos // 1000)


def GetLabelsFromFleetspeak(client_id):
  """Returns labels for a Fleetspeak-enabled client.

  Fleetspeak-enabled clients delegate labeling to Fleetspeak, as opposed to
  using labels in the GRR config.

  Args:
    client_id: Id of the client to fetch Fleetspeak labels for.

  Returns:
    A list of client labels.
  """
  res = fleetspeak_connector.CONN.outgoing.ListClients(
      admin_pb2.ListClientsRequest(client_ids=[GRRIDToFleetspeakID(client_id)]))
  if not res.clients or not res.clients[0].labels:
    return []

  grr_labels = []
  label_prefix = config.CONFIG["Server.fleetspeak_label_prefix"]
  for fs_label in res.clients[0].labels:
    if (fs_label.service_name != "client" or
        (label_prefix and not fs_label.label.startswith(label_prefix))):
      continue
    try:
      grr_labels.append(fleetspeak_connector.label_map[fs_label.label])
    except KeyError:
      grr_labels.append(fs_label.label)

  return grr_labels
