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

#include <string>
#include <vector>

class HttpContext;
class HttpSession;
class HttpVHost;
class HtaRules;
struct HtaStamp;

/** One directive, or one block with its children, as written in the file. */
struct HtaDirective
{
    std::string                 m_sName;
    std::string                 m_sLine;    //the text after continuation lines are joined
    std::vector<std::string>    m_args;
    std::vector<HtaDirective>   m_children;
    int                         m_iLine;
    bool                        m_bBlock;

    HtaDirective()
        : m_iLine(0)
        , m_bBlock(false)
    {}
};

/**
 * Applies .htaccess files to runtime contexts and evaluates at request time the
 * directives that the rest of the server has no equivalent for.
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

    /** Parses text into directives. Returns the line of the first error or 0. */
    static int parse(const char *pText, std::vector<HtaDirective> &directives);

    static void applyEnv(HttpSession *pSession);

    /** Returns 0 to let the request continue, otherwise an HTTP status. */
    static int  checkAccess(HttpSession *pSession);
    static int  checkRedirect(HttpSession *pSession);

    /** The rules are created by load() and owned by a context. */
    static void releaseRules(HtaRules *pRules);
    static void setParentRules(HtaRules *pRules, const HtaRules *pParent);
};

#endif
