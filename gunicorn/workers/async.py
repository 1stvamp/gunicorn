# -*- coding: utf-8 -
#
# This file is part of gunicorn released under the MIT license. 
# See the NOTICE for more information.

from __future__ import with_statement

import errno
import os
import select
import socket
import traceback

import gunicorn.http as http
import gunicorn.http.wsgi as wsgi
import gunicorn.util as util
import gunicorn.workers.base as base

ALREADY_HANDLED = object()

class AsyncWorker(base.Worker):

    def __init__(self, *args, **kwargs):
        super(AsyncWorker, self).__init__(*args, **kwargs)
        self.worker_connections = self.cfg.worker_connections
    
    def timeout_ctx(self):
        raise NotImplementedError()

    def start_accepting(self):
        raise NotImplementedError

    def stop_accepting(self):
        raise NotImplementedError

    def wakeup(self):
        """\
        Wake up the worker by writing to the PIPE
        """
        try:
            os.write(self.PIPE[1], '.')
        except IOError, e:
            if e.errno not in [errno.EAGAIN, errno.EINTR]:
                raise

    def handle_quit(self, *args):
        self.wakeup()
        super(AsyncWorker, self).handle_quit(*args)
    
    def handle_exit(self, *args):
        self.wakeup()
        super(AsyncWorker, self).handle_exit(*args)

    def run(self):
        self.start_accepting()

        try:
            while self.alive:
                self.notify()
                if self.ppid != os.getppid():
                    self.log.info("Parent changed, shutting down: %s" % self)
                    break
                
                try:
                    ret = select.select([self.PIPE[0]], [], [],
                            self.timeout)
                    if ret[0]:
                        break
                except select.error, e:
                    if e[0] not in [errno.EAGAIN, errno.EINTR]:
                        raise
        except KeyboardInterrupt:
            pass

        self.notify()
        self.stop_accepting()

    def handle(self, client, addr):
        try:
            parser = http.RequestParser(client)
            try:
                while True:
                    req = None
                    with self.timeout_ctx():
                        req = parser.next()
                    if not req:
                        break
                    self.handle_request(req, client, addr)
            except StopIteration:
                pass
        except socket.error, e:
            if e[0] not in (errno.EPIPE, errno.ECONNRESET):
                self.log.exception("Socket error processing request.")
            else:
                if e[0] == errno.ECONNRESET:
                    self.log.debug("Ignoring connection reset")
                else:
                    self.log.debug("Ignoring EPIPE")
        except Exception, e:
            self.log.exception("General error processing request.")
            self.handle_error(client, e)
        finally:
            util.close(client)

    def handle_request(self, req, sock, addr):
        try:
            debug = self.cfg.debug or False
            self.cfg.pre_request(self, req)
            resp, environ = wsgi.create(req, sock, addr, self.address, self.cfg)
            self.nr += 1
            if self.alive and self.nr >= self.max_requests:
                self.log.info("Autorestarting worker after current request.")
                resp.force_close()
                self.alive = False
            respiter = self.wsgi(environ, resp.start_response)
            if respiter == ALREADY_HANDLED:
                return False
            for item in respiter:
                resp.write(item)
            resp.close()
            if hasattr(respiter, "close"):
                respiter.close()
            if req.should_close():
                raise StopIteration()
        except StopIteration:
            raise
        except Exception, e:
            #Only send back traceback in HTTP in debug mode.
            self.handle_error(sock, e)
            return False
        finally:
            try:
                self.cfg.post_request(self, req)
            except:
                pass
        return True
