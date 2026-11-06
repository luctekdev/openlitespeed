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
#ifndef HTACCESS_H
#define HTACCESS_H

class HttpContext;
class HttpVHost;
struct HtaStamp;

/**
 * Applies .htaccess files to runtime contexts.
 */
class Htaccess
{
public:
    /** Fills stamp with the identity of the .htaccess in pDir, if there is one. */
    static void stat(const char *pDir, HtaStamp &stamp);

    /**
     * Configures pContext from the .htaccess in its directory. Must run before
     * HttpContext::inherit(), which merges the parent values into what is set here.
     */
    static int load(HttpVHost *pVHost, HttpContext *pContext);
};

#endif
