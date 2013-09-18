#!/usr/bin/env python
# Copyright 2011 Google Inc. All Rights Reserved.
"""This is a development server for running the UI."""


import logging
import socket
import SocketServer
from wsgiref import simple_server

from django.core.handlers import wsgi

# pylint: disable=unused-import,g-bad-import-order
from grr.gui import django_lib
from grr.lib import server_plugins
from grr.gui import plot_lib
# pylint: enable=unused-import,g-bad-import-order

from grr.lib import config_lib
from grr.lib import flags
from grr.lib import startup


class ThreadingDjango(SocketServer.ThreadingMixIn, simple_server.WSGIServer):
  address_family = socket.AF_INET6


def main(_):
  """Run the main test harness."""
  config_lib.CONFIG.AddContext(
      "AdminUI Context",
      "Context applied when running the admin user interface GUI.")
  startup.Init()

  # Start up a server in another thread
  base_url = "http://%s:%d" % (config_lib.CONFIG["AdminUI.bind"],
                               config_lib.CONFIG["AdminUI.port"])
  logging.info("Base URL is %s", base_url)

  # Make a simple reference implementation WSGI server
  server = simple_server.make_server(config_lib.CONFIG["AdminUI.bind"],
                                     config_lib.CONFIG["AdminUI.port"],
                                     wsgi.WSGIHandler(),
                                     server_class=ThreadingDjango)

  server.serve_forever()

if __name__ == "__main__":
  flags.StartMain(main)
