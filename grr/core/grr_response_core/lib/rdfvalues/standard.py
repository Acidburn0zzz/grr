#!/usr/bin/env python
"""Standard RDFValues."""

from __future__ import absolute_import
from __future__ import division

from __future__ import unicode_literals

import re

from future.builtins import str
from future.moves.urllib import parse as urlparse
from typing import Text

from grr_response_core.lib import config_lib
from grr_response_core.lib import rdfvalue
from grr_response_core.lib.rdfvalues import structs as rdf_structs
from grr_response_core.lib.util import precondition
from grr_response_proto import jobs_pb2
from grr_response_proto import sysinfo_pb2


class RegularExpression(rdfvalue.RDFString):
  """A semantic regular expression."""

  context_help_url = ("investigating-with-grr/flows/"
                      "literal-and-regex-matching.html#regex-matches")

  def __init__(self, initializer=None):
    super(RegularExpression, self).__init__(initializer=initializer)
    # Try compiling the pattern right away to fail fast for pattern errors.
    self._Regex()

  def _Regex(self):
    return re.compile(self._value, flags=re.I | re.S | re.M)

  def Search(self, text):
    """Search the text for our value."""
    if isinstance(text, rdfvalue.RDFString):
      text = str(text)

    return self._Regex().search(text)

  def Match(self, text):
    if isinstance(text, rdfvalue.RDFString):
      text = str(text)

    return self._Regex().match(text)

  def FindIter(self, text):
    if isinstance(text, rdfvalue.RDFString):
      text = str(text)

    return self._Regex().finditer(text)


class LiteralExpression(rdfvalue.RDFBytes):
  """A RDFBytes literal for use in GrepSpec."""

  context_help_url = ("investigating-with-grr/flows/"
                      "literal-and-regex-matching.html#literal-matches")


class EmailAddress(rdfvalue.RDFString):
  """An email address must be well formed."""

  _EMAIL_REGEX = re.compile(r"[^@]+@([^@]+)$")

  def ParseFromBytes(self, value):
    super(EmailAddress, self).ParseFromBytes(value)

    self._match = self._EMAIL_REGEX.match(self._value)
    if not self._match:
      raise ValueError("Email address %r not well formed." % self._value)


class DomainEmailAddress(EmailAddress):
  """A more restricted email address may only address the domain."""

  def ParseFromBytes(self, value):
    super(DomainEmailAddress, self).ParseFromBytes(value)

    # TODO(user): dependency loop with
    # core/grr_response_core/grr/config/client.py.
    # pylint: disable=protected-access
    domain = config_lib._CONFIG["Logging.domain"]
    # pylint: enable=protected-access
    if domain and self._match.group(1) != domain:
      raise ValueError("Email address '%s' does not belong to the configured "
                       "domain '%s'" % (self._match.group(1), domain))


class AuthenticodeSignedData(rdf_structs.RDFProtoStruct):
  protobuf = jobs_pb2.AuthenticodeSignedData


class PersistenceFile(rdf_structs.RDFProtoStruct):
  protobuf = jobs_pb2.PersistenceFile
  rdf_deps = [
      "PathSpec",  # TODO(user): dependency loop.
      rdfvalue.RDFURN,
  ]


class URI(rdf_structs.RDFProtoStruct):
  """Represets a URI with its individual components seperated."""
  protobuf = sysinfo_pb2.URI

  def ParseFromBytes(self, value):
    precondition.AssertType(value, bytes)
    self.ParseFromHumanReadable(value.decode("utf-8"))

  def ParseFromHumanReadable(self, value):
    precondition.AssertType(value, Text)
    url = urlparse.urlparse(value)

    if url.scheme:
      self.transport = url.scheme
    if url.netloc:
      self.host = url.netloc
    if url.path:
      self.path = url.path
    if url.query:
      self.query = url.query
    if url.fragment:
      self.fragment = url.fragment

  def SerializeToBytes(self):
    return self.SerializeToHumanReadable().encode("utf-8")

  def SerializeToHumanReadable(self):
    parts = (self.transport, self.host, self.path, self.query, self.fragment)
    return urlparse.urlunsplit(parts)
