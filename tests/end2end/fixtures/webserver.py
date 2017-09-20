# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2015-2017 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Fixtures for the server webserver."""

import re
import sys
import json
import os.path
import http.client

import attr
import pytest
from PyQt5.QtCore import pyqtSignal, QUrl

from end2end.fixtures import testprocess

from qutebrowser.utils import utils


class Request(testprocess.Line):

    """A parsed line from the flask log output.

    Attributes:
        verb/path/status: Parsed from the log output.
    """

    def __init__(self, data):
        super().__init__(data)
        try:
            parsed = json.loads(data)
        except ValueError:
            raise testprocess.InvalidLine(data)

        assert isinstance(parsed, dict)
        assert set(parsed.keys()) == {'path', 'verb', 'status'}

        self.verb = parsed['verb']

        path = parsed['path']
        self.path = '/' if path == '/' else path.rstrip('/')

        self.status = parsed['status']
        self._check_status()

    def _check_status(self):
        """Check if the http status is what we expected."""
        # WORKAROUND for https://github.com/PyCQA/pylint/issues/399 (?)
        # pylint: disable=no-member
        path_to_statuses = {
            '/favicon.ico': [http.client.NOT_FOUND],
            '/does-not-exist': [http.client.NOT_FOUND],
            '/does-not-exist-2': [http.client.NOT_FOUND],
            '/404': [http.client.NOT_FOUND],

            '/redirect-later': [http.client.FOUND],
            '/redirect-self': [http.client.FOUND],
            '/redirect-to': [http.client.FOUND],
            '/relative-redirect': [http.client.FOUND],
            '/absolute-redirect': [http.client.FOUND],

            '/cookies/set': [http.client.FOUND],

            '/500-inline': [http.client.INTERNAL_SERVER_ERROR],
        }
        for i in range(15):
            path_to_statuses['/redirect/{}'.format(i)] = [http.client.FOUND]
        for suffix in ['', '1', '2', '3', '4', '5', '6']:
            key = '/basic-auth/user{}/password{}'.format(suffix, suffix)
            path_to_statuses[key] = [http.client.UNAUTHORIZED, http.client.OK]

        default_statuses = [http.client.OK, http.client.NOT_MODIFIED]

        sanitized = QUrl('http://localhost' + self.path).path()  # Remove ?foo
        expected_statuses = path_to_statuses.get(sanitized, default_statuses)
        if self.status not in expected_statuses:
            raise AssertionError(
                "{} loaded with status {} but expected {}".format(
                    sanitized, self.status,
                    ' / '.join(repr(e) for e in expected_statuses)))

    def __eq__(self, other):
        return NotImplemented


@attr.s(frozen=True, cmp=False, hash=True)
class ExpectedRequest:

    """Class to compare expected requests easily."""

    verb = attr.ib()
    path = attr.ib()

    @classmethod
    def from_request(cls, request):
        """Create an ExpectedRequest from a Request."""
        return cls(request.verb, request.path)

    def __eq__(self, other):
        if isinstance(other, (Request, ExpectedRequest)):
            return self.verb == other.verb and self.path == other.path
        else:
            return NotImplemented


class WebserverProcess(testprocess.Process):

    """Abstraction over a running Flask server process.

    Reads the log from its stdout and parses it.

    Signals:
        new_request: Emitted when there's a new request received.
    """

    new_request = pyqtSignal(Request)
    Request = Request  # So it can be used from the fixture easily.
    ExpectedRequest = ExpectedRequest

    KEYS = ['verb', 'path']

    def __init__(self, script, parent=None):
        super().__init__(parent)
        self._script = script
        self.port = utils.random_port()
        self.new_data.connect(self.new_request)

    def get_requests(self):
        """Get the requests to the server during this test."""
        requests = self._get_data()
        return [r for r in requests if r.path != '/favicon.ico']

    def _parse_line(self, line):
        self._log(line)
        started_re = re.compile(r' \* Running on https?://127\.0\.0\.1:{}/ '
                                r'\(Press CTRL\+C to quit\)'.format(self.port))
        if started_re.fullmatch(line):
            self.ready.emit()
            return None
        return Request(line)

    def _executable_args(self):
        if hasattr(sys, 'frozen'):
            executable = os.path.join(os.path.dirname(sys.executable),
                                      self._script)
            args = []
        else:
            executable = sys.executable
            py_file = os.path.join(os.path.dirname(__file__),
                                   self._script + '.py')
            args = [py_file]
        return executable, args

    def _default_args(self):
        return [str(self.port)]

    def cleanup(self):
        """Clean up and shut down the process."""
        self.proc.terminate()
        self.proc.waitForFinished()


@pytest.fixture(scope='session', autouse=True)
def server(qapp):
    """Fixture for an server object which ensures clean setup/teardown."""
    server = WebserverProcess('webserver_sub')
    server.start()
    yield server
    server.cleanup()


@pytest.fixture(autouse=True)
def server_after_test(server, request):
    """Fixture to clean server request list after each test."""
    request.node._server_log = server.captured_log
    yield
    server.after_test()


@pytest.fixture
def ssl_server(request, qapp):
    """Fixture for a webserver with a self-signed SSL certificate.

    This needs to be explicitly used in a test, and overwrites the server log
    used in that test.
    """
    server = WebserverProcess('webserver_sub_ssl')
    request.node._server_log = server.captured_log
    server.start()
    yield server
    server.after_test()
    server.cleanup()
