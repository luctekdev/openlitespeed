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
#include "htaccess.h"

#include <http/httpcontext.h>
#include <http/httplog.h>
#include <http/httpvhost.h>
#include <http/rewriteengine.h>
#include <http/rewriterule.h>
#include <log4cxx/logger.h>
#include <lsdef.h>
#include <util/autostr.h>
#include <log4cxx/tmplogid.h>

#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

//The file belongs to the site owner, who must not be able to exhaust memory.
#define HTA_MAX_SIZE    (1024 * 1024)


static int htaPath(const char *pDir, char *pBuf, int size)
{
    int len = snprintf(pBuf, size, "%s.htaccess", pDir);
    return (len > 0 && len < size) ? len : LS_FAIL;
}


void Htaccess::stat(const char *pDir, HtaStamp &stamp)
{
    char achPath[PATH_MAX];
    struct stat st;
    memset(&stamp, 0, sizeof(stamp));
    if (htaPath(pDir, achPath, sizeof(achPath)) == LS_FAIL
        || ::stat(achPath, &st) != 0 || !S_ISREG(st.st_mode))
        return;
    stamp.m_iPresent = 1;
    stamp.m_tmMod = st.st_mtime;
#if defined(__linux__)
    stamp.m_lNsec = st.st_mtim.tv_nsec;
#endif
    stamp.m_iSize = st.st_size;
    stamp.m_iIno = st.st_ino;
}


static int readFile(const char *pPath, AutoStr2 &content)
{
    int fd = open(pPath, O_RDONLY | O_NONBLOCK);
    if (fd == -1)
        return LS_FAIL;
    struct stat st;
    int ret = LS_FAIL;
    if (fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_size <= HTA_MAX_SIZE
        && content.prealloc(st.st_size + 1))
    {
        ssize_t len = read(fd, content.buf(), st.st_size);
        if (len >= 0)
        {
            content.buf()[len] = 0;
            content.setLen(len);
            ret = 0;
        }
    }
    close(fd);
    return ret;
}


int Htaccess::load(HttpVHost *pVHost, HttpContext *pContext)
{
    const char *pDir = pContext->getLocation();
    HtaStamp &stamp = pContext->getHtaStamp();
    time_t tmCheck = stamp.m_tmCheck;
    //Taken before reading: an edit in between triggers one more reload, not none.
    stat(pDir, stamp);
    stamp.m_tmCheck = tmCheck;
    if (!stamp.m_iPresent)
        return 0;

    char achPath[PATH_MAX];
    AutoStr2 content;
    if (htaPath(pDir, achPath, sizeof(achPath)) == LS_FAIL
        || readFile(achPath, content) == LS_FAIL)
    {
        LS_WARN("[%s] Cannot read %s.", pVHost->getName(), achPath);
        return LS_FAIL;
    }

    RewriteRuleList *pRules = new RewriteRuleList();
    RewriteRule::setLogger(NULL, TmpLogId::getLogId());
    char *pText = content.buf();
    if (RewriteEngine::parseRules(pText, pRules, pVHost->getRewriteMaps(),
                                  pContext) == 0 && pRules->begin())
        pContext->setRewriteRules(pRules);
    else
        delete pRules;
    return 0;
}
