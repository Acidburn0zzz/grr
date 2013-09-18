#!/usr/bin/env python
"""AFF4 RDFValue implementations for client information.

This module contains the RDFValue implementations used to communicate with the
client.
"""

from hashlib import sha256

import re
import socket

from grr.lib import rdfvalue
from grr.lib import type_info
from grr.lib import utils

from grr.lib.rdfvalues import protodict
from grr.lib.rdfvalues import standard
from grr.lib.rdfvalues import structs

from grr.proto import flows_pb2
from grr.proto import jobs_pb2
from grr.proto import knowledge_base_pb2
from grr.proto import sysinfo_pb2


class ClientURN(rdfvalue.RDFURN):
  """A client urn has to have a specific form."""

  # Valid client urns must match this expression.
  CLIENT_ID_RE = re.compile(r"^(aff4:/)?C\.[0-9a-fA-F]{16}$")

  def __init__(self, initializer=None, age=None):
    """Constructor.

    Args:
      initializer: A string or another RDFURN.
      age: The age of this entry.
    """
    # If we are initialized from another URN we need to validate it.
    if isinstance(initializer, rdfvalue.RDFURN):
      super(ClientURN, self).__init__(initializer=None, age=age)
      self.ParseFromString(initializer.SerializeToString())

    else:
      super(ClientURN, self).__init__(initializer=initializer, age=age)

  def ParseFromString(self, value):
    if not self.Validate(value):
      raise type_info.TypeValueError("Client urn malformed: %s" % value)

    return super(ClientURN, self).ParseFromString(value)

  @classmethod
  def Validate(cls, value):
    if value:
      return bool(cls.CLIENT_ID_RE.match(str(value)))

    return False

  @classmethod
  def FromPublicKey(cls, public_key):
    """An alternate constructor which generates a new client id."""
    return cls("C.%s" % (
        sha256(public_key).digest()[:8].encode("hex")))

  def Add(self, path, age=None):
    """Add a relative stem to the current value and return a new RDFURN.

    Note that this returns an RDFURN, not a ClientURN since the resulting object
    would not pass validation.

    Args:
      path: A string containing a relative path.
      age: The age of the object. If None set to current time.

    Returns:
       A new RDFURN that can be chained.

    Raises:
       ValueError: if the path component is not a string.
    """
    if not isinstance(path, basestring):
      raise ValueError("Only strings should be added to a URN.")

    result = rdfvalue.RDFURN(self.Copy(age))
    result.Update(path=utils.JoinPath(self._urn.path, path))

    return result


# These are objects we store as attributes of the client.
class Filesystem(structs.RDFProtoStruct):
  """A filesystem on the client.

  This class describes a filesystem mounted on the client.
  """
  protobuf = sysinfo_pb2.Filesystem


class Filesystems(protodict.RDFValueArray):
  """An array of client filesystems.

  This is used to represent the list of valid filesystems on the client.
  """
  rdf_type = Filesystem


class FolderInformation(rdfvalue.RDFProtoStruct):
  """Representation of Window's special folders information for a User.

  Windows maintains a list of "Special Folders" which are used to organize a
  user's home directory. Knowledge about these is required in order to resolve
  the location of user specific items, e.g. the Temporary folder, or the
  Internet cache.
  """
  protobuf = jobs_pb2.FolderInformation


class User(rdfvalue.RDFProtoStruct):
  """A user of the client system.

  This stores information related to a specific user of the client system.
  """
  protobuf = jobs_pb2.User


class Users(protodict.RDFValueArray):
  """A list of user accounts on the client system."""
  rdf_type = User


class KnowledgeBase(rdfvalue.RDFProtoStruct):
  """Information about the system and users."""
  protobuf = knowledge_base_pb2.KnowledgeBase


class KnowledgeBaseUser(rdfvalue.RDFProtoStruct):
  """Information about the users."""
  protobuf = knowledge_base_pb2.KnowledgeBaseUser


class NetworkEndpoint(rdfvalue.RDFProtoStruct):
  protobuf = sysinfo_pb2.NetworkEndpoint


class NetworkConnection(rdfvalue.RDFProtoStruct):
  """Information about a single network connection."""
  protobuf = sysinfo_pb2.NetworkConnection


class Connections(protodict.RDFValueArray):
  """A list of connections on the host."""
  rdf_type = NetworkConnection


class NetworkAddress(rdfvalue.RDFProtoStruct):
  """A network address."""
  protobuf = jobs_pb2.NetworkAddress

  @property
  def human_readable_address(self):
    if self.human_readable:
      return self.human_readable
    else:
      if self.address_type == rdfvalue.NetworkAddress.Family.INET:
        return socket.inet_ntop(socket.AF_INET, self.packed_bytes)
      else:
        return socket.inet_ntop(socket.AF_INET6, self.packed_bytes)


class MacAddress(rdfvalue.RDFBytes):
  """A MAC address."""

  @property
  def human_readable_address(self):
    return self._value.encode("hex")


class Interface(rdfvalue.RDFProtoStruct):
  """A network interface on the client system."""
  protobuf = jobs_pb2.Interface

  def GetIPAddresses(self):
    """Return a list of IP addresses."""
    results = []
    for address in self.addresses:
      if address.human_readable:
        results.append(address.human_readable)
      else:
        if address.address_type == rdfvalue.NetworkAddress.Family.INET:
          results.append(socket.inet_ntop(socket.AF_INET,
                                          address.packed_bytes))
        else:
          results.append(socket.inet_ntop(socket.AF_INET6,
                                          address.packed_bytes))
    return results


class Interfaces(protodict.RDFValueArray):
  """The list of interfaces on a host."""
  rdf_type = Interface

  def GetIPAddresses(self):
    """Return the list of IP addresses."""
    results = []
    for interface in self:
      results += interface.GetIPAddresses()
    return results


# DEPRECATED - do not use.
class GRRConfig(rdfvalue.RDFProtoStruct):
  """The configuration of a GRR Client."""
  protobuf = jobs_pb2.GRRConfig


class ClientInformation(rdfvalue.RDFProtoStruct):
  """The GRR client information."""
  protobuf = jobs_pb2.ClientInformation


class CpuSeconds(rdfvalue.RDFProtoStruct):
  """CPU usage is reported as both a system and user components."""
  protobuf = jobs_pb2.CpuSeconds


class CpuSample(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.CpuSample

  # The total number of samples this sample represents - used for running
  # averages.
  _total_samples = 1

  def Average(self, sample):
    """Updates this sample from the new sample."""
    # For now we only average the cpu_percent
    self.timestamp = sample.timestamp
    self.user_cpu_time = sample.user_cpu_time
    self.system_cpu_time = sample.system_cpu_time

    # Update the average from the new sample point.
    self.cpu_percent = (
        self.cpu_percent * self._total_samples + sample.cpu_percent)/(
            self._total_samples + 1)

    self._total_samples += 1


class IOSample(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.IOSample

  def Average(self, sample):
    """Updates this sample from the new sample."""
    # For now we just copy the new sample to ourselves.
    self.timestamp = sample.timestamp
    self.read_bytes = sample.read_bytes
    self.write_bytes = sample.write_bytes


class ClientStats(rdfvalue.RDFProtoStruct):
  """A client stat object."""
  protobuf = jobs_pb2.ClientStats

  def DownsampleList(self, samples, interval):
    """Reduces samples at different timestamps into interval time bins."""
    # The current bin we are calculating (initializes to the first bin).
    current_bin = None

    # The last sample we see in the current bin. We always emit the last sample
    # in the current bin.
    last_sample_seen = None

    for sample in samples:
      timestamp = sample.timestamp.AsSecondsFromEpoch()

      # The time bin this sample belongs to.
      time_bin = timestamp - (timestamp % interval)

      # Initialize to the first bin, but do not emit anything yet until we
      # switch bins.
      if current_bin is None:
        current_bin = time_bin
        last_sample_seen = sample

      # If the current sample is not in the current bin we switch bins.
      elif current_bin != time_bin and last_sample_seen:
        # Emit the last seen bin.
        yield last_sample_seen

        # Move to the next bin.
        current_bin = time_bin
        last_sample_seen = sample

      else:
        # Update the last_sample_seen with the new sample taking averages if
        # needed.
        last_sample_seen.Average(sample)

    # Emit the last sample especially as part of the last bin.
    if last_sample_seen:
      yield last_sample_seen

  def DownSample(self, sampling_interval=60):
    """Downsamples the data to save space.

    Args:
      sampling_interval: The sampling interval in seconds.
    """
    self.cpu_samples = self.DownsampleList(self.cpu_samples, sampling_interval)
    self.io_samples = self.DownsampleList(self.io_samples, sampling_interval)


class DriverInstallTemplate(rdfvalue.RDFProtoStruct):
  """Driver specific information controlling default installation.

  This is sent to the client to instruct the client how to install this driver.
  """
  protobuf = jobs_pb2.DriverInstallTemplate


class BufferReference(rdfvalue.RDFProtoStruct):
  """Stores information about a buffer in a file on the client."""
  protobuf = jobs_pb2.BufferReference

  def __eq__(self, other):
    return self.data == other


class Process(rdfvalue.RDFProtoStruct):
  """Represent a process on the client."""
  protobuf = sysinfo_pb2.Process


class Processes(protodict.RDFValueArray):
  """A list of processes on the system."""
  rdf_type = Process


class SoftwarePackage(rdfvalue.RDFProtoStruct):
  """Represent an installed package on the client."""
  protobuf = sysinfo_pb2.SoftwarePackage


class SoftwarePackages(protodict.RDFValueArray):
  """A list of installed packages on the system."""
  rdf_type = SoftwarePackage


class StatMode(rdfvalue.RDFInteger):
  """The mode of a file."""
  data_store_type = "unsigned_integer"

  def __unicode__(self):
    """Pretty print the file mode."""
    mode_template = "rwx" * 3
    # Strip the "0b"
    mode = bin(int(self))[2:]
    mode = mode[-9:]
    mode = "0" * (9-len(mode)) + mode

    bits = []
    for i in range(len(mode_template)):
      if mode[i] == "1":
        bit = mode_template[i]
      else:
        bit = "-"

      bits.append(bit)

    return "".join(bits)


class Iterator(rdfvalue.RDFProtoStruct):
  """An Iterated client action is one which can be resumed on the client."""
  protobuf = jobs_pb2.Iterator


class StatEntry(rdfvalue.RDFProtoStruct):
  """Represent an extended stat response."""
  protobuf = jobs_pb2.StatEntry


class FindSpec(rdfvalue.RDFProtoStruct):
  """A find specification."""
  protobuf = jobs_pb2.FindSpec

  dependencies = dict(RegularExpression=standard.RegularExpression)

  def Validate(self):
    """Ensure the pathspec is valid."""
    self.pathspec.Validate()

    # When using OS and TSK we must only perform find on directories.
    if (self.pathspec.last.pathtype in [self.pathspec.PathType.OS,
                                        self.pathspec.PathType.TSK] and
        self.pathspec.last.path[-1] not in ("/", "\\")):
      raise ValueError("Find can only operate on directories "
                       "(There must be a final /)")

    if (self.HasField("start_time") and self.HasField("end_time") and
        self.start_time > self.end_time):
      raise ValueError("Start time must be before end time.")

    if not self.path_regex and not self.data_regex:
      raise ValueError("A Find specification can not contain both an empty "
                       "path regex and an empty data regex")


class LogMessage(rdfvalue.RDFProtoStruct):
  """A log message sent from the client to the server."""
  protobuf = jobs_pb2.PrintStr


class EchoRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.PrintStr


class ExecuteBinaryRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.ExecuteBinaryRequest


class ExecuteBinaryResponse(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.ExecuteBinaryResponse


class ExecutePythonRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.ExecutePythonRequest


class ExecutePythonResponse(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.ExecutePythonResponse


class ExecuteRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.ExecuteRequest


class CopyPathToFileRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.CopyPathToFile


class ExecuteResponse(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.ExecuteResponse


class Uname(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.Uname


class StartupInfo(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.StartupInfo


class SendFileRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.SendFileRequest

  def Validate(self):
    self.pathspec.Validate()

    if not self.host:
      raise ValueError("A host must be specified.")


class ListDirRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.ListDirRequest


class FingerprintTuple(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.FingerprintTuple


class FingerprintRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.FingerprintRequest

  def AddRequest(self, *args, **kw):
    self.tuples.Append(*args, **kw)


class FingerprintResponse(rdfvalue.RDFProtoStruct):
  """Proto containing dicts with hashes."""
  protobuf = jobs_pb2.FingerprintResponse

  def GetFingerprint(self, name):
    """Gets the first fingerprint type from the protobuf."""
    for result in self.results:
      if result.GetItem("name") == name:
        return result


class GrepSpec(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.GrepSpec

  def Validate(self):
    self.target.Validate()


class BareGrepSpec(rdfvalue.RDFProtoStruct):
  """A GrepSpec without a target."""
  protobuf = flows_pb2.BareGrepSpec


class WMIRequest(rdfvalue.RDFProtoStruct):
  protobuf = jobs_pb2.WmiRequest


class LaunchdJob(rdfvalue.RDFProtoStruct):
  protobuf = sysinfo_pb2.LaunchdJob


class Service(rdfvalue.RDFProtoStruct):
  """Structure of a running service."""
  protobuf = sysinfo_pb2.Service

  rdf_map = dict(osx_launchd=LaunchdJob)


class Services(protodict.RDFValueArray):
  """Structure of a running service."""
  rdf_type = Service


class ClientResources(rdfvalue.RDFProtoStruct):
  """An RDFValue class representing the client resource usage."""
  protobuf = jobs_pb2.ClientResources

  dependencies = dict(ClientURN=ClientURN,
                      RDFURN=rdfvalue.RDFURN)


# Start of the Registry Specific Data types
class RunKey(rdfvalue.RDFProtoStruct):
  protobuf = sysinfo_pb2.RunKey


class RunKeyEntry(protodict.RDFValueArray):
  """Structure of a Run Key entry with keyname, filepath, and last written."""
  rdf_type = RunKey


class MRUFile(rdfvalue.RDFProtoStruct):
  protobuf = sysinfo_pb2.MRUFile


class MRUFolder(protodict.RDFValueArray):
  """Structure describing Most Recently Used (MRU) files."""
  rdf_type = MRUFile


class AFF4ObjectSummary(rdfvalue.RDFProtoStruct):
  """A summary of an AFF4 object.

  AFF4Collection objects maintain a list of AFF4 objects. To make it easier to
  filter and search these collections, we need to store a summary of each AFF4
  object inside the collection (so we do not need to open every object for
  filtering).

  This summary is maintained in the RDFProto instance.
  """
  protobuf = jobs_pb2.AFF4ObjectSummary


class ClientCrash(rdfvalue.RDFProtoStruct):
  """Details of a client crash."""
  protobuf = jobs_pb2.ClientCrash


class ClientSummary(rdfvalue.RDFProtoStruct):
  """Object containing client's summary data."""
  protobuf = jobs_pb2.ClientSummary
