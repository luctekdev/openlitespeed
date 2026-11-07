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
#include "wpprotect.h"

#include <netinet/in.h>
#include <stdint.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/socket.h>

#define WP_SLOTS    (1 << 16)

struct WpSlot
{
    uint32_t    m_iKey;
    uint32_t    m_iCount;
    uint32_t    m_tmWindow;
    uint32_t    m_tmBlocked;
};

static WpSlot *s_pSlots = NULL;


int WpProtect::init()
{
    if (s_pSlots)
        return 0;
    void *p = mmap(NULL, WP_SLOTS * sizeof(WpSlot), PROT_READ | PROT_WRITE,
                   MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED)
        return -1;
    s_pSlots = (WpSlot *)p;
    return 0;
}


static int endsWith(const char *pURI, int len, const char *pName, int nameLen)
{
    return len >= nameLen
           && strncasecmp(pURI + len - nameLen, pName, nameLen) == 0;
}


int WpProtect::isLoginUri(const char *pURI, int len)
{
    return endsWith(pURI, len, "/wp-login.php", 13)
           || endsWith(pURI, len, "/xmlrpc.php", 11);
}


/** FNV-1a. Never returns 0, which marks a free slot. */
static uint32_t hashAddr(const struct sockaddr *pAddr)
{
    const unsigned char *p;
    int len;
    if (pAddr->sa_family == AF_INET6)
    {
        //A /64 is what one IPv6 client controls.
        p = (const unsigned char *)&((const struct sockaddr_in6 *)pAddr)->sin6_addr;
        len = 8;
    }
    else
    {
        p = (const unsigned char *)&((const struct sockaddr_in *)pAddr)->sin_addr;
        len = 4;
    }
    uint32_t hash = 2166136261u;
    for (int i = 0; i < len; ++i)
        hash = (hash ^ p[i]) * 16777619u;
    return hash ? hash : 1;
}


int WpProtect::attempt(const struct sockaddr *pAddr, int limit, int window,
                       int block, time_t now)
{
    if (!s_pSlots || !pAddr
        || (pAddr->sa_family != AF_INET && pAddr->sa_family != AF_INET6))
        return 0;

    uint32_t key = hashAddr(pAddr);
    uint32_t tmNow = (uint32_t)now;
    WpSlot *pSlot = &s_pSlots[key & (WP_SLOTS - 1)];

    //Races between workers can lose a count or two, which does not matter here.
    if (pSlot->m_iKey != key || tmNow - pSlot->m_tmWindow >= (uint32_t)window)
    {
        if (pSlot->m_iKey == key && pSlot->m_tmBlocked > tmNow)
            return pSlot->m_tmBlocked - tmNow;
        pSlot->m_iKey = key;
        pSlot->m_tmWindow = tmNow;
        pSlot->m_tmBlocked = 0;
        __sync_lock_test_and_set(&pSlot->m_iCount, 0);
    }
    if (pSlot->m_tmBlocked > tmNow)
        return pSlot->m_tmBlocked - tmNow;
    if (__sync_add_and_fetch(&pSlot->m_iCount, 1) <= (uint32_t)limit)
        return 0;
    pSlot->m_tmBlocked = tmNow + block;
    return block;
}
