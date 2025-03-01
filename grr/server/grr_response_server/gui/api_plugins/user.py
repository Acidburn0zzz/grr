#!/usr/bin/env python
"""API handlers for user-related data and actions."""
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import email
import functools
import itertools
import logging

from future.builtins import str
from future.utils import itervalues
import jinja2

from grr_response_core import config

from grr_response_core.lib import rdfvalue
from grr_response_core.lib import utils

from grr_response_core.lib.rdfvalues import client as rdf_client
from grr_response_core.lib.rdfvalues import paths as rdf_paths
from grr_response_core.lib.rdfvalues import structs as rdf_structs
from grr_response_proto import user_pb2
from grr_response_proto.api import user_pb2 as api_user_pb2

from grr_response_server import access_control
from grr_response_server import cronjobs
from grr_response_server import data_store
from grr_response_server import email_alerts
from grr_response_server import flow
from grr_response_server import notification as notification_lib
from grr_response_server.databases import db
from grr_response_server.flows.general import administrative
from grr_response_server.gui import api_call_handler_base
from grr_response_server.gui import approval_checks

from grr_response_server.gui.api_plugins import client as api_client

from grr_response_server.gui.api_plugins import cron as api_cron
from grr_response_server.gui.api_plugins import flow as api_flow
from grr_response_server.gui.api_plugins import hunt as api_hunt

from grr_response_server.rdfvalues import objects as rdf_objects


class ApprovalNotFoundError(api_call_handler_base.ResourceNotFoundError):
  """Raised when a specific approval object could not be found."""


class GUISettings(rdf_structs.RDFProtoStruct):
  protobuf = user_pb2.GUISettings


class ApiNotificationClientReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationClientReference
  rdf_deps = [
      api_client.ApiClientId,
  ]


class ApiNotificationHuntReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationHuntReference
  rdf_deps = [
      api_hunt.ApiHuntId,
  ]


class ApiNotificationCronReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationCronReference
  rdf_deps = [
      api_cron.ApiCronJobId,
  ]


class ApiNotificationFlowReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationFlowReference
  rdf_deps = [
      api_client.ApiClientId,
      api_flow.ApiFlowId,
  ]


class ApiNotificationVfsReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationVfsReference
  rdf_deps = [
      api_client.ApiClientId,
  ]


class ApiNotificationClientApprovalReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationClientApprovalReference
  rdf_deps = [
      api_client.ApiClientId,
  ]


class ApiNotificationHuntApprovalReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationHuntApprovalReference
  rdf_deps = [
      api_hunt.ApiHuntId,
  ]


class ApiNotificationCronJobApprovalReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationCronJobApprovalReference
  rdf_deps = [
      api_cron.ApiCronJobId,
  ]


class ApiNotificationUnknownReference(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiNotificationUnknownReference
  rdf_deps = [
      rdfvalue.RDFURN,
  ]


class ApiNotificationReference(rdf_structs.RDFProtoStruct):
  """Object reference used in ApiNotifications."""

  protobuf = api_user_pb2.ApiNotificationReference
  rdf_deps = [
      ApiNotificationClientReference,
      ApiNotificationClientApprovalReference,
      ApiNotificationCronJobApprovalReference,
      ApiNotificationCronReference,
      ApiNotificationFlowReference,
      ApiNotificationHuntApprovalReference,
      ApiNotificationHuntReference,
      ApiNotificationUnknownReference,
      ApiNotificationVfsReference,
  ]

  def InitFromObjectReference(self, ref):
    if ref.reference_type == ref.Type.UNSET:
      self.type = self.Type.UNSET

    elif ref.reference_type == ref.Type.CLIENT:
      self.type = self.Type.CLIENT
      self.client.client_id = ref.client.client_id

    elif ref.reference_type == ref.Type.HUNT:
      self.type = self.Type.HUNT
      self.hunt.hunt_id = ref.hunt.hunt_id

    elif ref.reference_type == ref.Type.FLOW:
      self.type = self.Type.FLOW
      self.flow.client_id = ref.flow.client_id
      self.flow.flow_id = ref.flow.flow_id

    elif ref.reference_type == ref.Type.CRON_JOB:
      self.type = self.Type.CRON
      self.cron.cron_job_id = ref.cron_job.cron_job_id

    elif ref.reference_type == ref.Type.VFS_FILE:
      self.type = self.Type.VFS
      self.vfs.client_id = ref.vfs_file.client_id

      if ref.vfs_file.path_type == rdf_objects.PathInfo.PathType.UNSET:
        raise ValueError(
            "Can't init from VFS_FILE object reference with unset path_type.")

      self.vfs.vfs_path = ref.vfs_file.ToPath()

    elif ref.reference_type == ref.Type.APPROVAL_REQUEST:
      ref_ar = ref.approval_request

      if ref_ar.approval_type == ref_ar.ApprovalType.APPROVAL_TYPE_NONE:
        raise ValueError("Can't init from APPROVAL_REQUEST object reference "
                         "with unset approval_type.")
      elif ref_ar.approval_type == ref_ar.ApprovalType.APPROVAL_TYPE_CLIENT:
        self.type = self.Type.CLIENT_APPROVAL
        self.client_approval.approval_id = ref_ar.approval_id
        self.client_approval.username = ref_ar.requestor_username
        self.client_approval.client_id = ref_ar.subject_id
      elif ref_ar.approval_type == ref_ar.ApprovalType.APPROVAL_TYPE_HUNT:
        self.type = self.Type.HUNT_APPROVAL
        self.hunt_approval.approval_id = ref_ar.approval_id
        self.hunt_approval.username = ref_ar.requestor_username
        self.hunt_approval.hunt_id = ref_ar.subject_id
      elif ref_ar.approval_type == ref_ar.ApprovalType.APPROVAL_TYPE_CRON_JOB:
        self.type = self.Type.CRON_JOB_APPROVAL
        self.cron_job_approval.approval_id = ref_ar.approval_id
        self.cron_job_approval.username = ref_ar.requestor_username
        self.cron_job_approval.cron_job_id = ref_ar.subject_id
      else:
        raise ValueError("Unexpected APPROVAL_REQUEST object reference type "
                         "value: %d" % ref_ar.approval_type)
    else:
      raise ValueError("Unexpected reference type: %d" % ref.type)

    return self


class ApiNotification(rdf_structs.RDFProtoStruct):
  """Represents a user notification."""

  protobuf = api_user_pb2.ApiNotification
  rdf_deps = [
      ApiNotificationReference,
      rdfvalue.RDFDatetime,
  ]

  def _GetUrnComponents(self, notification):
    # Still display if subject doesn't get set, this will appear in the GUI with
    # a target of "None"
    urn = "/"
    if notification.subject is not None:
      urn = notification.subject

    path = rdfvalue.RDFURN(urn)
    return path.Path().split("/")[1:]

  def InitFromNotification(self, notification, is_pending=False):
    """Initializes this object from an existing notification.

    Args:
      notification: A rdfvalues.flows.Notification object.
      is_pending: Indicates whether the user has already seen this notification
        or not.

    Returns:
      The current instance.
    """
    self.timestamp = notification.timestamp
    self.message = notification.message
    self.subject = str(notification.subject)
    self.is_pending = is_pending

    reference_type_enum = ApiNotificationReference.Type

    # Please see the comments to notification.Notify implementation
    # for the details of notification.type format. Short summary:
    # notification.type may be one of legacy values (i.e. "ViewObject") or
    # have a format of "[legacy value]:[new-style notification type]", i.e.
    # "ViewObject:TYPE_CLIENT_INTERROGATED".
    if ":" in notification.type:
      legacy_type, new_type = notification.type.split(":", 2)
      self.notification_type = new_type
    else:
      legacy_type = notification.type

    # TODO(user): refactor notifications, so that we send a meaningful
    # notification from the start, so that we don't have to do the
    # bridging/conversion/guessing here.
    components = self._GetUrnComponents(notification)
    if legacy_type == "Discovery":
      self.reference.type = reference_type_enum.CLIENT
      self.reference.client = ApiNotificationClientReference(
          client_id=components[0])
    elif legacy_type == "ViewObject":
      if len(components) >= 2 and components[0] == "hunts":
        self.reference.type = reference_type_enum.HUNT
        self.reference.hunt.hunt_id = components[1]
      elif len(components) >= 2 and components[0] == "cron":
        self.reference.type = reference_type_enum.CRON
        self.reference.cron.cron_job_id = components[1]
      elif len(components) >= 3 and components[1] == "flows":
        self.reference.type = reference_type_enum.FLOW
        self.reference.flow.flow_id = components[2]
        self.reference.flow.client_id = components[0]
      elif len(components) == 1 and rdf_client.ClientURN.Validate(
          components[0]):
        self.reference.type = reference_type_enum.CLIENT
        self.reference.client.client_id = components[0]
      else:
        if notification.subject:
          path = notification.subject.Path()
          for prefix in itervalues(rdf_paths.PathSpec.AFF4_PREFIXES):
            part = "/%s%s" % (components[0], prefix)
            if path.startswith(part):
              self.reference.type = reference_type_enum.VFS
              self.reference.vfs.client_id = components[0]
              self.reference.vfs.vfs_path = (prefix +
                                             path[len(part):]).lstrip("/")
              break

        if self.reference.type != reference_type_enum.VFS:
          self.reference.type = reference_type_enum.UNKNOWN
          self.reference.unknown.subject_urn = notification.subject

    elif legacy_type == "FlowStatus":
      if not components or not rdf_client.ClientURN.Validate(components[0]):
        self.reference.type = reference_type_enum.UNKNOWN
        self.reference.unknown.subject_urn = notification.subject
      else:
        self.reference.type = reference_type_enum.FLOW
        self.reference.flow.flow_id = notification.source.Basename()
        self.reference.flow.client_id = components[0]

    # TODO(user): refactor GrantAccess notification so that we don't have
    # to infer approval type from the URN.
    elif legacy_type == "GrantAccess":
      if rdf_client.ClientURN.Validate(components[1]):
        self.reference.type = reference_type_enum.CLIENT_APPROVAL
        self.reference.client_approval.client_id = components[1]
        self.reference.client_approval.approval_id = components[-1]
        self.reference.client_approval.username = components[-2]
      elif components[1] == "hunts":
        self.reference.type = reference_type_enum.HUNT_APPROVAL
        self.reference.hunt_approval.hunt_id = components[2]
        self.reference.hunt_approval.approval_id = components[-1]
        self.reference.hunt_approval.username = components[-2]
      elif components[1] == "cron":
        self.reference.type = reference_type_enum.CRON_JOB_APPROVAL
        self.reference.cron_job_approval.cron_job_id = components[2]
        self.reference.cron_job_approval.approval_id = components[-1]
        self.reference.cron_job_approval.username = components[-2]

    else:
      self.reference.type = reference_type_enum.UNKNOWN
      self.reference.unknown.subject_urn = notification.subject
      self.reference.unknown.source_urn = notification.source

    return self

  def InitFromUserNotification(self, notification):
    self.timestamp = notification.timestamp
    self.notification_type = notification.notification_type
    self.message = notification.message
    self.is_pending = (notification.state == notification.State.STATE_PENDING)
    try:
      self.reference = ApiNotificationReference().InitFromObjectReference(
          notification.reference)
    except ValueError as e:
      logging.exception(
          "Can't initialize notification from an "
          "object reference: %s", e)
      # In case of any initialization issue, simply create an empty reference.
      self.reference = ApiNotificationReference(
          type=ApiNotificationReference.Type.UNSET)

    return self


class ApiGrrUserInterfaceTraits(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiGrrUserInterfaceTraits

  def EnableAll(self):
    for type_descriptor in self.type_infos:
      self.Set(type_descriptor.name, True)

    return self


class ApiGrrUser(rdf_structs.RDFProtoStruct):
  """API object describing the user."""

  protobuf = api_user_pb2.ApiGrrUser
  rdf_deps = [
      ApiGrrUserInterfaceTraits,
      GUISettings,
  ]

  def InitFromDatabaseObject(self, db_obj):
    self.username = db_obj.username

    if db_obj.user_type == db_obj.UserType.USER_TYPE_ADMIN:
      self.user_type = self.UserType.USER_TYPE_ADMIN
    else:
      self.user_type = self.UserType.USER_TYPE_STANDARD

    self.settings.mode = db_obj.ui_mode
    self.settings.canary_mode = db_obj.canary_mode

    return self


def _InitApiApprovalFromDatabaseObject(api_approval, db_obj):
  """Initializes Api(Client|Hunt|CronJob)Approval from the database object."""

  api_approval.id = db_obj.approval_id
  api_approval.requestor = db_obj.requestor_username
  api_approval.reason = db_obj.reason

  api_approval.notified_users = sorted(db_obj.notified_users)
  api_approval.email_cc_addresses = sorted(db_obj.email_cc_addresses)
  api_approval.email_message_id = db_obj.email_message_id

  api_approval.approvers = sorted([g.grantor_username for g in db_obj.grants])

  try:
    approval_checks.CheckApprovalRequest(db_obj)
    api_approval.is_valid = True
  except access_control.UnauthorizedAccess as e:
    api_approval.is_valid_message = str(e)
    api_approval.is_valid = False

  return api_approval


class ApiClientApproval(rdf_structs.RDFProtoStruct):
  """API client approval object."""

  protobuf = api_user_pb2.ApiClientApproval
  rdf_deps = [
      api_client.ApiClient,
  ]

  def InitFromDatabaseObject(self, db_obj, approval_subject_obj=None):
    if not approval_subject_obj:
      approval_subject_obj = data_store.REL_DB.ReadClientFullInfo(
          db_obj.subject_id)
    self.subject = api_client.ApiClient().InitFromClientInfo(
        approval_subject_obj)

    return _InitApiApprovalFromDatabaseObject(self, db_obj)

  @property
  def subject_title(self):
    return u"GRR client %s (%s)" % (self.subject.client_id,
                                    self.subject.knowledge_base.fqdn)

  @property
  def review_url_path(self):
    return "/".join([
        "users", self.requestor, "approvals", "client",
        str(self.subject.client_id), self.id
    ])

  @property
  def subject_url_path(self):
    return "/clients/%s" % str(self.subject.client_id)

  def ObjectReference(self):
    at = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CLIENT
    return rdf_objects.ObjectReference(
        reference_type=rdf_objects.ObjectReference.Type.APPROVAL_REQUEST,
        approval_request=rdf_objects.ApprovalRequestReference(
            approval_type=at,
            approval_id=self.id,
            subject_id=str(self.subject.client_id),
            requestor_username=self.requestor))


class ApiHuntApproval(rdf_structs.RDFProtoStruct):
  """API hunt approval object."""

  protobuf = api_user_pb2.ApiHuntApproval
  rdf_deps = [
      api_flow.ApiFlow,
      api_hunt.ApiHunt,
  ]

  def InitFromDatabaseObject(self, db_obj, approval_subject_obj=None):
    _InitApiApprovalFromDatabaseObject(self, db_obj)

    if not approval_subject_obj:
      approval_subject_obj = data_store.REL_DB.ReadHuntObject(db_obj.subject_id)
      approval_subject_counters = data_store.REL_DB.ReadHuntCounters(
          db_obj.subject_id)
      self.subject = api_hunt.ApiHunt().InitFromHuntObject(
          approval_subject_obj,
          hunt_counters=approval_subject_counters,
          with_full_summary=True)
    original_object = approval_subject_obj.original_object

    if original_object.object_type == "FLOW_REFERENCE":
      original_flow = data_store.REL_DB.ReadFlowObject(
          original_object.flow_reference.client_id,
          original_object.flow_reference.flow_id)
      self.copied_from_flow = api_flow.ApiFlow().InitFromFlowObject(
          original_flow)
    elif original_object.object_type == "HUNT_REFERENCE":
      original_hunt = data_store.REL_DB.ReadHuntObject(
          original_object.hunt_reference.hunt_id)
      original_hunt_counters = data_store.REL_DB.ReadHuntCounters(
          original_object.hunt_reference.hunt_id)
      self.copied_from_hunt = api_hunt.ApiHunt().InitFromHuntObject(
          original_hunt,
          hunt_counters=original_hunt_counters,
          with_full_summary=True)

    return self

  @property
  def subject_title(self):
    return u"hunt %s" % (self.subject.hunt_id)

  @property
  def review_url_path(self):
    return "/".join([
        "users", self.requestor, "approvals", "hunt",
        str(self.subject.hunt_id), self.id
    ])

  @property
  def subject_url_path(self):
    return "/hunts/%s" % str(self.subject.hunt_id)

  def ObjectReference(self):
    at = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_HUNT
    return rdf_objects.ObjectReference(
        reference_type=rdf_objects.ObjectReference.Type.APPROVAL_REQUEST,
        approval_request=rdf_objects.ApprovalRequestReference(
            approval_type=at,
            approval_id=self.id,
            subject_id=str(self.subject.hunt_id),
            requestor_username=self.requestor))


class ApiCronJobApproval(rdf_structs.RDFProtoStruct):
  """API cron job approval object."""

  protobuf = api_user_pb2.ApiCronJobApproval
  rdf_deps = [
      api_cron.ApiCronJob,
  ]

  def _FillInSubject(self, job_id, approval_subject_obj=None):
    if not approval_subject_obj:
      approval_subject_obj = cronjobs.CronManager().ReadJob(job_id)
      self.subject = api_cron.ApiCronJob.InitFromObject(approval_subject_obj)

  def InitFromDatabaseObject(self, db_obj, approval_subject_obj=None):
    _InitApiApprovalFromDatabaseObject(self, db_obj)
    self._FillInSubject(
        db_obj.subject_id, approval_subject_obj=approval_subject_obj)
    return self

  @property
  def subject_title(self):
    return u"a cron job %s" % (self.subject.cron_job_id)

  @property
  def review_url_path(self):
    return "/".join([
        "users", self.requestor, "approvals", "cron-job",
        str(self.subject.cron_job_id), self.id
    ])

  @property
  def subject_url_path(self):
    return "/crons/%s" % str(self.subject.cron_job_id)

  def ObjectReference(self):
    at = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CRON_JOB
    return rdf_objects.ObjectReference(
        reference_type=rdf_objects.ObjectReference.Type.APPROVAL_REQUEST,
        approval_request=rdf_objects.ApprovalRequestReference(
            approval_type=at,
            approval_id=self.id,
            subject_id=str(self.subject.cron_job_id),
            requestor_username=self.requestor))


class ApiCreateApprovalHandlerBase(api_call_handler_base.ApiCallHandler):
  """Base class for all Create*Approval handlers."""

  # objects.ApprovalRequest.ApprovalType value describing the approval type.
  approval_type = None

  def SendApprovalEmail(self, approval):
    if not config.CONFIG.Get("Email.send_approval_emails"):
      return

    subject_template = jinja2.Template(
        "Approval for {{ user }} to access {{ subject }}.", autoescape=True)
    subject = subject_template.render(
        user=utils.SmartUnicode(approval.requestor),
        subject=utils.SmartUnicode(approval.subject_title))

    template = jinja2.Template(
        """
<html><body><h1>Approval to access
<a href='{{ admin_ui }}/#/{{ approval_url }}'>{{ subject_title }}</a>
requested.</h1>

The user "{{ username }}" has requested access to
<a href='{{ admin_ui }}/#/{{ approval_url }}'>{{ subject_title }}</a>
for the purpose of <em>{{ reason }}</em>.

Please click <a href='{{ admin_ui }}/#/{{ approval_url }}'>
here
</a> to review this request and then grant access.

<p>Thanks,</p>
<p>{{ signature }}</p>
<p>{{ image|safe }}</p>
</body></html>""",
        autoescape=True)

    body = template.render(
        username=utils.SmartUnicode(approval.requestor),
        reason=utils.SmartUnicode(approval.reason),
        admin_ui=utils.SmartUnicode(config.CONFIG["AdminUI.url"]),
        subject_title=utils.SmartUnicode(approval.subject_title),
        approval_url=utils.SmartUnicode(approval.review_url_path),
        # If you feel like it, add a funny cat picture here :)
        image=utils.SmartUnicode(config.CONFIG["Email.approval_signature"]),
        signature=utils.SmartUnicode(config.CONFIG["Email.signature"]))

    email_alerts.EMAIL_ALERTER.SendEmail(
        ",".join(approval.notified_users),
        approval.requestor,
        subject,
        body,
        is_html=True,
        cc_addresses=",".join(approval.email_cc_addresses),
        message_id=approval.email_message_id)

  def CreateApprovalNotification(self, approval):
    for user in approval.notified_users:
      try:
        notification_lib.Notify(
            user.strip(), self.__class__.approval_notification_type,
            "Please grant access to %s" % approval.subject_title,
            approval.ObjectReference())
      except db.UnknownGRRUserError:
        # The relational db does not allow sending notifications to users that
        # don't exist. This should happen rarely but we need to catch this case.
        logging.error("Notification sent for unknown user %s!", user.strip())

  def Handle(self, args, token=None):
    if not args.approval.reason:
      raise ValueError("Approval reason can't be empty.")

    expiry = config.CONFIG["ACL.token_expiry"]

    request = rdf_objects.ApprovalRequest(
        requestor_username=token.username,
        approval_type=self.__class__.approval_type,
        reason=args.approval.reason,
        notified_users=args.approval.notified_users,
        email_cc_addresses=args.approval.email_cc_addresses,
        subject_id=args.BuildSubjectId(),
        expiration_time=rdfvalue.RDFDatetime.Now() + expiry,
        email_message_id=email.utils.make_msgid())
    request.approval_id = data_store.REL_DB.WriteApprovalRequest(request)

    data_store.REL_DB.GrantApproval(
        approval_id=request.approval_id,
        requestor_username=token.username,
        grantor_username=token.username)

    result = self.__class__.result_type().InitFromDatabaseObject(request)

    self.SendApprovalEmail(result)
    self.CreateApprovalNotification(result)
    return result


class ApiListApprovalsHandlerBase(api_call_handler_base.ApiCallHandler):
  """Renders list of all user approvals."""

  def _FilterRelationalApprovalRequests(self, approval_requests,
                                        approval_create_fn, state):
    for ar in approval_requests:
      client_approval = approval_create_fn(ar)

      if state == ApiListClientApprovalsArgs.State.ANY:
        yield client_approval
      elif state == ApiListClientApprovalsArgs.State.VALID:
        if client_approval.is_valid:
          yield client_approval
      elif state == ApiListClientApprovalsArgs.State.INVALID:
        if not client_approval.is_valid:
          yield client_approval


class ApiGetApprovalHandlerBase(api_call_handler_base.ApiCallHandler):
  """Base class for all Get*Approval handlers."""

  # objects.ApprovalRequest.ApprovalType value describing the approval type.
  approval_type = None

  def Handle(self, args, token=None):
    try:
      approval_obj = data_store.REL_DB.ReadApprovalRequest(
          args.username, args.approval_id)
    except db.UnknownApprovalRequestError:
      raise ApprovalNotFoundError(
          "No approval with id=%s, type=%s, subject=%s could be found." %
          (args.approval_id, self.__class__.approval_type,
           args.BuildSubjectId()))

    if approval_obj.approval_type != self.__class__.approval_type:
      raise ValueError(
          "Unexpected approval type: %s, expected: %s" %
          (approval_obj.approval_type, self.__class__.approval_type))

    if approval_obj.subject_id != args.BuildSubjectId():
      raise ValueError("Unexpected subject id: %s, expected: %s" %
                       (approval_obj.subject_id, args.BuildSubjectId()))

    return self.__class__.result_type().InitFromDatabaseObject(approval_obj)


class ApiGrantApprovalHandlerBase(api_call_handler_base.ApiCallHandler):
  """Base class reused by all client approval handlers."""

  # objects.ApprovalRequest.ApprovalType value describing the approval type.
  approval_type = None

  # Class to be used to grant the approval. Should be set by a subclass.
  approval_grantor = None

  def SendGrantEmail(self, approval, token=None):
    if not config.CONFIG.Get("Email.send_approval_emails"):
      return

    subject_template = jinja2.Template(
        "Approval for {{ user }} to access {{ subject }}.", autoescape=True)
    subject = subject_template.render(
        user=utils.SmartUnicode(approval.requestor),
        subject=utils.SmartUnicode(approval.subject_title))

    template = jinja2.Template(
        """
<html><body><h1>Access to
<a href='{{ admin_ui }}/#/{{ subject_url }}'>{{ subject_title }}</a>
granted.</h1>

The user {{ username }} has granted access to
<a href='{{ admin_ui }}/#/{{ subject_url }}'>{{ subject_title }}</a> for the
purpose of <em>{{ reason }}</em>.

Please click <a href='{{ admin_ui }}/#/{{ subject_url }}'>here</a> to access it.

<p>Thanks,</p>
<p>{{ signature }}</p>
</body></html>""",
        autoescape=True)
    body = template.render(
        subject_title=utils.SmartUnicode(approval.subject_title),
        username=utils.SmartUnicode(token.username),
        reason=utils.SmartUnicode(approval.reason),
        admin_ui=utils.SmartUnicode(config.CONFIG["AdminUI.url"].strip("/")),
        subject_url=utils.SmartUnicode(approval.subject_url_path.strip("/")),
        signature=utils.SmartUnicode(config.CONFIG["Email.signature"]))

    # Email subject should match approval request, and we add message id
    # references so they are grouped together in a thread by gmail.
    headers = {
        "In-Reply-To": approval.email_message_id,
        "References": approval.email_message_id
    }
    email_alerts.EMAIL_ALERTER.SendEmail(
        approval.requestor,
        token.username,
        subject,
        body,
        is_html=True,
        cc_addresses=",".join(approval.email_cc_addresses),
        headers=headers)

  def CreateGrantNotification(self, approval, token=None):
    notification_lib.Notify(
        approval.requestor, self.__class__.approval_notification_type,
        "%s has granted you access to %s." %
        (token.username, approval.subject_title),
        approval.subject.ObjectReference())

  def Handle(self, args, token=None):
    if not args.username:
      raise ValueError("username can't be empty.")

    try:
      data_store.REL_DB.GrantApproval(args.username, args.approval_id,
                                      token.username)

      approval_obj = data_store.REL_DB.ReadApprovalRequest(
          args.username, args.approval_id)
    except db.UnknownApprovalRequestError:
      raise ApprovalNotFoundError(
          "No approval with id=%s, type=%s, subject=%s could be found." %
          (args.approval_id, self.__class__.approval_type,
           args.BuildSubjectId()))

    result = self.__class__.result_type().InitFromDatabaseObject(approval_obj)

    self.SendGrantEmail(result, token=token)
    self.CreateGrantNotification(result, token=token)
    return result


class ApiClientApprovalArgsBase(rdf_structs.RDFProtoStruct):
  """Base class for client approvals."""

  __abstract = True  # pylint: disable=g-bad-name

  def BuildSubjectId(self):
    return str(self.client_id)


class ApiCreateClientApprovalArgs(ApiClientApprovalArgsBase):
  protobuf = api_user_pb2.ApiCreateClientApprovalArgs
  rdf_deps = [
      ApiClientApproval,
      api_client.ApiClientId,
  ]


class ApiCreateClientApprovalHandler(ApiCreateApprovalHandlerBase):
  """Creates new user client approval and notifies requested approvers."""

  args_type = ApiCreateClientApprovalArgs
  result_type = ApiClientApproval

  approval_type = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CLIENT
  approval_notification_type = (
      rdf_objects.UserNotification.Type.TYPE_CLIENT_APPROVAL_REQUESTED)

  def Handle(self, args, token=None):
    result = super(ApiCreateClientApprovalHandler, self).Handle(
        args, token=token)

    if args.keep_client_alive:
      flow.StartFlow(
          client_id=str(args.client_id),
          flow_cls=administrative.KeepAlive,
          creator=token.username,
          duration=3600)

    return result


class ApiGetClientApprovalArgs(ApiClientApprovalArgsBase):
  protobuf = api_user_pb2.ApiGetClientApprovalArgs
  rdf_deps = [
      api_client.ApiClientId,
  ]


class ApiGetClientApprovalHandler(ApiGetApprovalHandlerBase):
  """Returns details about an approval for a given client and reason."""

  args_type = ApiGetClientApprovalArgs
  result_type = ApiClientApproval

  approval_type = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CLIENT


class ApiGrantClientApprovalArgs(ApiClientApprovalArgsBase):
  protobuf = api_user_pb2.ApiGrantClientApprovalArgs
  rdf_deps = [
      api_client.ApiClientId,
  ]


class ApiGrantClientApprovalHandler(ApiGrantApprovalHandlerBase):
  """Handle for GrantClientApproval requests."""

  args_type = ApiGrantClientApprovalArgs
  result_type = ApiClientApproval

  approval_type = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CLIENT
  approval_notification_type = (
      rdf_objects.UserNotification.Type.TYPE_CLIENT_APPROVAL_GRANTED)


class ApiListClientApprovalsArgs(ApiClientApprovalArgsBase):
  protobuf = api_user_pb2.ApiListClientApprovalsArgs
  rdf_deps = [
      api_client.ApiClientId,
  ]


class ApiListClientApprovalsResult(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListClientApprovalsResult
  rdf_deps = [
      ApiClientApproval,
  ]


class ApiListClientApprovalsHandler(ApiListApprovalsHandlerBase):
  """Returns list of user's clients approvals."""

  args_type = ApiListClientApprovalsArgs
  result_type = ApiListClientApprovalsResult

  def _CheckClientId(self, client_id, approval):
    subject = approval.Get(approval.Schema.SUBJECT)
    return subject.Basename() == client_id

  def _CheckState(self, state, approval):
    try:
      approval.CheckAccess(approval.token)
      is_valid = True
    except access_control.UnauthorizedAccess:
      is_valid = False

    if state == ApiListClientApprovalsArgs.State.VALID:
      return is_valid

    if state == ApiListClientApprovalsArgs.State.INVALID:
      return not is_valid

  def _BuildFilter(self, args):
    filters = []

    if args.client_id:
      filters.append(functools.partial(self._CheckClientId, args.client_id))

    if args.state:
      filters.append(functools.partial(self._CheckState, args.state))

    if filters:

      def Filter(approval):
        for f in filters:
          if not f(approval):
            return False

        return True

      return Filter
    else:
      return lambda approval: True  # Accept all by default.

  def Handle(self, args, token=None):
    subject_id = None
    if args.client_id:
      subject_id = str(args.client_id)

    approvals = sorted(
        data_store.REL_DB.ReadApprovalRequests(
            token.username,
            rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CLIENT,
            subject_id=subject_id,
            include_expired=True),
        key=lambda ar: ar.timestamp,
        reverse=True)
    approvals = self._FilterRelationalApprovalRequests(
        approvals, lambda ar: ApiClientApproval().InitFromDatabaseObject(ar),
        args.state)

    if not args.count:
      end = None
    else:
      end = args.offset + args.count
    items = list(itertools.islice(approvals, args.offset, end))
    api_client.UpdateClientsFromFleetspeak([a.subject for a in items])

    return ApiListClientApprovalsResult(items=items)


class ApiHuntApprovalArgsBase(rdf_structs.RDFProtoStruct):

  __abstract = True  # pylint: disable=g-bad-name

  def BuildSubjectId(self):
    return str(self.hunt_id)


class ApiCreateHuntApprovalArgs(ApiHuntApprovalArgsBase):
  protobuf = api_user_pb2.ApiCreateHuntApprovalArgs
  rdf_deps = [
      ApiHuntApproval,
      api_hunt.ApiHuntId,
  ]


class ApiCreateHuntApprovalHandler(ApiCreateApprovalHandlerBase):
  """Creates new user hunt approval and notifies requested approvers."""

  args_type = ApiCreateHuntApprovalArgs
  result_type = ApiHuntApproval

  approval_type = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_HUNT
  approval_notification_type = (
      rdf_objects.UserNotification.Type.TYPE_HUNT_APPROVAL_REQUESTED)


class ApiGetHuntApprovalArgs(ApiHuntApprovalArgsBase):
  protobuf = api_user_pb2.ApiGetHuntApprovalArgs
  rdf_deps = [
      api_hunt.ApiHuntId,
  ]


class ApiGetHuntApprovalHandler(ApiGetApprovalHandlerBase):
  """Returns details about approval for a given hunt, user and approval id."""

  args_type = ApiGetHuntApprovalArgs
  result_type = ApiHuntApproval

  approval_type = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_HUNT


class ApiGrantHuntApprovalArgs(ApiHuntApprovalArgsBase):
  protobuf = api_user_pb2.ApiGrantHuntApprovalArgs
  rdf_deps = [
      api_hunt.ApiHuntId,
  ]


class ApiGrantHuntApprovalHandler(ApiGrantApprovalHandlerBase):
  """Handle for GrantHuntApproval requests."""

  args_type = ApiGrantHuntApprovalArgs
  result_type = ApiHuntApproval

  approval_type = rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_HUNT
  approval_notification_type = (
      rdf_objects.UserNotification.Type.TYPE_HUNT_APPROVAL_GRANTED)


class ApiListHuntApprovalsArgs(ApiHuntApprovalArgsBase):
  protobuf = api_user_pb2.ApiListHuntApprovalsArgs


class ApiListHuntApprovalsResult(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListHuntApprovalsResult
  rdf_deps = [
      ApiHuntApproval,
  ]


class ApiListHuntApprovalsHandler(ApiListApprovalsHandlerBase):
  """Returns list of user's hunts approvals."""

  args_type = ApiListHuntApprovalsArgs
  result_type = ApiListHuntApprovalsResult

  def Handle(self, args, token=None):
    approvals = sorted(
        data_store.REL_DB.ReadApprovalRequests(
            token.username,
            rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_HUNT,
            subject_id=None,
            include_expired=True),
        key=lambda ar: ar.timestamp,
        reverse=True)

    if not args.count:
      end = None
    else:
      end = args.offset + args.count

    items = [
        ApiHuntApproval().InitFromDatabaseObject(ar)
        for ar in approvals[args.offset:end]
    ]

    return ApiListHuntApprovalsResult(items=items)


class ApiCronJobApprovalArgsBase(rdf_structs.RDFProtoStruct):
  """Base class for Cron Job approvals."""

  __abstract = True  # pylint: disable=g-bad-name

  def BuildSubjectId(self):
    return str(self.cron_job_id)


class ApiCreateCronJobApprovalArgs(ApiCronJobApprovalArgsBase):
  protobuf = api_user_pb2.ApiCreateCronJobApprovalArgs
  rdf_deps = [
      api_cron.ApiCronJobId,
      ApiCronJobApproval,
  ]


class ApiCreateCronJobApprovalHandler(ApiCreateApprovalHandlerBase):
  """Creates new user cron approval and notifies requested approvers."""

  args_type = ApiCreateCronJobApprovalArgs
  result_type = ApiCronJobApproval

  approval_type = (
      rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CRON_JOB)
  approval_notification_type = (
      rdf_objects.UserNotification.Type.TYPE_CRON_JOB_APPROVAL_REQUESTED)


class ApiGetCronJobApprovalArgs(ApiCronJobApprovalArgsBase):
  protobuf = api_user_pb2.ApiGetCronJobApprovalArgs
  rdf_deps = [
      api_cron.ApiCronJobId,
  ]


class ApiGetCronJobApprovalHandler(ApiGetApprovalHandlerBase):
  """Returns details about approval for a given cron, user and approval id."""

  args_type = ApiGetCronJobApprovalArgs
  result_type = ApiCronJobApproval

  approval_type = (
      rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CRON_JOB)


class ApiGrantCronJobApprovalArgs(ApiCronJobApprovalArgsBase):
  protobuf = api_user_pb2.ApiGrantCronJobApprovalArgs
  rdf_deps = [
      api_cron.ApiCronJobId,
  ]


class ApiGrantCronJobApprovalHandler(ApiGrantApprovalHandlerBase):
  """Handle for GrantCronJobApproval requests."""

  args_type = ApiGrantCronJobApprovalArgs
  result_type = ApiCronJobApproval

  approval_type = (
      rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CRON_JOB)
  approval_notification_type = (
      rdf_objects.UserNotification.Type.TYPE_CRON_JOB_APPROVAL_GRANTED)


class ApiListCronJobApprovalsArgs(ApiCronJobApprovalArgsBase):
  protobuf = api_user_pb2.ApiListCronJobApprovalsArgs


class ApiListCronJobApprovalsResult(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListCronJobApprovalsResult
  rdf_deps = [
      ApiCronJobApproval,
  ]


class ApiListCronJobApprovalsHandler(ApiListApprovalsHandlerBase):
  """Returns list of user's cron jobs approvals."""

  args_type = ApiListCronJobApprovalsArgs
  result_type = ApiListCronJobApprovalsResult

  def Handle(self, args, token=None):
    approvals = sorted(
        data_store.REL_DB.ReadApprovalRequests(
            token.username,
            rdf_objects.ApprovalRequest.ApprovalType.APPROVAL_TYPE_CRON_JOB,
            subject_id=None,
            include_expired=True),
        key=lambda ar: ar.timestamp,
        reverse=True)

    if not args.count:
      end = None
    else:
      end = args.offset + args.count

    items = [
        ApiCronJobApproval().InitFromDatabaseObject(ar)
        for ar in approvals[args.offset:end]
    ]

    return ApiListCronJobApprovalsResult(items=items)


class ApiGetOwnGrrUserHandler(api_call_handler_base.ApiCallHandler):
  """Renders current user settings."""

  result_type = ApiGrrUser

  def __init__(self, interface_traits=None):
    super(ApiGetOwnGrrUserHandler, self).__init__()
    self.interface_traits = interface_traits

  def Handle(self, unused_args, token=None):
    """Fetches and renders current user's settings."""

    result = ApiGrrUser(username=token.username)

    user_record = data_store.REL_DB.ReadGRRUser(token.username)
    result.InitFromDatabaseObject(user_record)

    result.interface_traits = (
        self.interface_traits or ApiGrrUserInterfaceTraits())

    return result


class ApiUpdateGrrUserHandler(api_call_handler_base.ApiCallHandler):
  """Sets current user settings."""

  args_type = ApiGrrUser

  def Handle(self, args, token=None):
    if args.username or args.HasField("interface_traits"):
      raise ValueError("Only user settings can be updated.")

    data_store.REL_DB.WriteGRRUser(
        token.username,
        ui_mode=args.settings.mode,
        canary_mode=args.settings.canary_mode)


class ApiGetPendingUserNotificationsCountResult(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiGetPendingUserNotificationsCountResult


class ApiGetPendingUserNotificationsCountHandler(
    api_call_handler_base.ApiCallHandler):
  """Returns the number of pending notifications for the current user."""

  result_type = ApiGetPendingUserNotificationsCountResult

  def Handle(self, args, token=None):
    """Fetches the pending notification count."""
    ns = list(
        data_store.REL_DB.ReadUserNotifications(
            token.username,
            state=rdf_objects.UserNotification.State.STATE_PENDING))
    return ApiGetPendingUserNotificationsCountResult(count=len(ns))


class ApiListPendingUserNotificationsArgs(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListPendingUserNotificationsArgs
  rdf_deps = [
      rdfvalue.RDFDatetime,
  ]


class ApiListPendingUserNotificationsResult(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListPendingUserNotificationsResult
  rdf_deps = [
      ApiNotification,
  ]


class ApiListPendingUserNotificationsHandler(
    api_call_handler_base.ApiCallHandler):
  """Returns pending notifications for the current user."""

  args_type = ApiListPendingUserNotificationsArgs
  result_type = ApiListPendingUserNotificationsResult

  def Handle(self, args, token=None):
    """Fetches the pending notifications."""
    ns = data_store.REL_DB.ReadUserNotifications(
        token.username,
        state=rdf_objects.UserNotification.State.STATE_PENDING,
        timerange=(args.timestamp, None))

    # TODO(user): Remove this, so that the order is reversed. This will
    # be an API-breaking change.
    ns = sorted(ns, key=lambda x: x.timestamp)

    # Make sure that only notifications with timestamp > args.timestamp
    # are returned.
    # Semantics of the API call (strict >) differs slightly from the
    # semantics of the db.ReadUserNotifications call (inclusive >=).
    if ns and ns[0].timestamp == args.timestamp:
      ns.pop(0)

    return ApiListPendingUserNotificationsResult(
        items=[ApiNotification().InitFromUserNotification(n) for n in ns])


class ApiDeletePendingUserNotificationArgs(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiDeletePendingUserNotificationArgs
  rdf_deps = [
      rdfvalue.RDFDatetime,
  ]


class ApiDeletePendingUserNotificationHandler(
    api_call_handler_base.ApiCallHandler):
  """Removes the pending notification with the given timestamp."""

  args_type = ApiDeletePendingUserNotificationArgs

  def Handle(self, args, token=None):
    """Deletes the notification from the pending notifications."""
    data_store.REL_DB.UpdateUserNotifications(
        token.username, [args.timestamp],
        state=rdf_objects.UserNotification.State.STATE_NOT_PENDING)


class ApiListAndResetUserNotificationsArgs(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListAndResetUserNotificationsArgs


class ApiListAndResetUserNotificationsResult(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListAndResetUserNotificationsResult
  rdf_deps = [
      ApiNotification,
  ]


class ApiListAndResetUserNotificationsHandler(
    api_call_handler_base.ApiCallHandler):
  """Returns the number of pending notifications for the current user."""

  args_type = ApiListAndResetUserNotificationsArgs
  result_type = ApiListAndResetUserNotificationsResult

  def Handle(self, args, token=None):
    """Fetches the user notifications."""
    back_timestamp = rdfvalue.RDFDatetime.Now() - rdfvalue.DurationSeconds(
        "180d")
    ns = data_store.REL_DB.ReadUserNotifications(
        token.username, timerange=(back_timestamp, None))

    pending_timestamps = [
        n.timestamp
        for n in ns
        if n.state == rdf_objects.UserNotification.State.STATE_PENDING
    ]
    data_store.REL_DB.UpdateUserNotifications(
        token.username,
        pending_timestamps,
        state=rdf_objects.UserNotification.State.STATE_NOT_PENDING)

    total_count = len(ns)
    if args.filter:
      ns = [n for n in ns if args.filter.lower() in n.message.lower()]

    if not args.count:
      args.count = 50

    start = args.offset
    end = args.offset + args.count

    api_notifications = []

    for n in ns[start:end]:
      try:
        api_notifications.append(ApiNotification().InitFromUserNotification(n))
      except ValueError as e:
        logging.error("Unable to convert notification %s: %s", n, e)

    return ApiListAndResetUserNotificationsResult(
        items=api_notifications, total_count=total_count)


class ApiListApproverSuggestionsArgs(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListApproverSuggestionsArgs
  rdf_deps = []


class ApproverSuggestion(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListApproverSuggestionsResult.ApproverSuggestion
  rdf_deps = []


class ApiListApproverSuggestionsResult(rdf_structs.RDFProtoStruct):
  protobuf = api_user_pb2.ApiListApproverSuggestionsResult
  rdf_deps = [ApproverSuggestion]


def _GetAllUsernames():
  return sorted(user.username for user in data_store.REL_DB.ReadGRRUsers())


class ApiListApproverSuggestionsHandler(api_call_handler_base.ApiCallHandler):
  """"List suggestions for approver usernames."""

  args_type = ApiListApproverSuggestionsArgs
  result_type = ApiListApproverSuggestionsResult

  def Handle(self, args, token=None):
    suggestions = []

    for username in _GetAllUsernames():
      if (username.startswith(args.username_query) and
          username != token.username):
        suggestions.append(ApproverSuggestion(username=username))

    return ApiListApproverSuggestionsResult(suggestions=suggestions)
