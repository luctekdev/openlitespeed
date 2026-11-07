/*****************************************************************************
*    Open LiteSpeed is an open source HTTP server.                           *
*                                                                            *
*    This program is free software: you can redistribute it and/or modify    *
*    it under the terms of the GNU General Public License as published by    *
*    the Free Software Foundation, either version 3 of the License, or       *
*    (at your option) any later version.                                     *
*                                                                            *
*    This program is distributed in the hope that it will be useful,         *
*    but WITHOUT ANY WARRANTY; without even the implied warranty of          *
*    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the            *
*    GNU General Public License for more details.                            *
*                                                                            *
*    You should have received a copy of the GNU General Public License       *
*    along with this program. If not, see http://www.gnu.org/licenses/.      *
*****************************************************************************/
#ifndef WPPROTECT_H
#define WPPROTECT_H

#include <time.h>

struct sockaddr;

/**
 * Limits the login attempts of each client on WordPress sites.
 *
 * The counters live in memory shared by all workers: a client lands on a
 * different worker per connection, so private counters would multiply the
 * limit by the number of workers. The table has a fixed size, and two clients
 * that share a slot only reset each other, so memory cannot grow and an attacker
 * gains nothing beyond what a new address would give.
 */
class WpProtect
{
public:
    /** Must run before the workers are forked. Returns 0 on success. */
    static int init();

    /** Tells whether the request is a login attempt. */
    static int isLoginUri(const char *pURI, int len);

    /**
     * Counts one attempt. Returns 0 to let it through, or the seconds the client
     * has to wait.
     */
    static int attempt(const struct sockaddr *pAddr, int limit, int window,
                       int block, time_t now);
};

#endif
