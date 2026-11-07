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

#include <http/htauth.h>
#include <http/httpcontext.h>
#include <http/httpheader.h>
#include <http/httplog.h>
#include <http/httpmethod.h>
#include <http/httpreq.h>
#include <http/httpsession.h>
#include <http/httpstatuscode.h>
#include <http/httpvhost.h>
#include <http/phpconfig.h>
#include <http/requestvars.h>
#include <http/rewriteengine.h>
#include <http/rewriterule.h>
#include <http/userdir.h>
#include <log4cxx/logger.h>
#include <log4cxx/tmplogid.h>
#include <lsdef.h>
#include <util/autostr.h>
#include <util/pcregex.h>

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

//The file belongs to the site owner, who must not be able to exhaust memory.
#define HTA_MAX_SIZE    (1024 * 1024)
#define HTA_MAX_DEPTH   16
#define HTA_PHP_TYPE    "application/x-httpd-php"
#define HTA_ALL_METHODS (~(uint64_t)0)

//The Apache version answered to <IfVersion>
#define HTA_VER_MAJOR   2
#define HTA_VER_MINOR   4
#define HTA_VER_PATCH   66


enum
{
    AZ_NEUTRAL,
    AZ_GRANTED,
    AZ_DENIED,
    AZ_NEED_USER
};


enum
{
    ORDER_UNSET,
    ORDER_DENY_ALLOW,
    ORDER_ALLOW_DENY,
    ORDER_MUTUAL
};


enum
{
    SATISFY_UNSET,
    SATISFY_ALL,
    SATISFY_ANY
};


/** An address or network, or a test on an environment variable. */
struct HtaAddr
{
    enum { ALL, NET, ENV, HOST } m_type;
    bool            m_bNegate;
    unsigned char   m_addr[16];
    int             m_iBits;
    std::string     m_sEnv;
    uint64_t        m_methods;

    HtaAddr()
        : m_type(ALL)
        , m_bNegate(false)
        , m_iBits(0)
        , m_methods(HTA_ALL_METHODS)
    {   memset(m_addr, 0, sizeof(m_addr));  }
};


struct HtaRequire
{
    enum Type
    {
        ALL_GRANTED, ALL_DENIED, IP, HOST, VALID_USER, USER, GROUP, ENV, METHOD,
        ANY, ALL_OF, NONE
    } m_type;
    bool                        m_bNegate;
    uint64_t                    m_methods;
    std::vector<std::string>    m_args;
    std::vector<HtaAddr>        m_addrs;
    std::vector<HtaRequire *>   m_children;
    AuthRequired               *m_pGroups;
    std::string                 m_sCond;    //only counts when this env is set

    explicit HtaRequire(Type type)
        : m_type(type)
        , m_bNegate(false)
        , m_methods(HTA_ALL_METHODS)
        , m_pGroups(NULL)
    {}

    ~HtaRequire()
    {
        for (size_t i = 0; i < m_children.size(); ++i)
            delete m_children[i];
        delete m_pGroups;
    }

private:
    HtaRequire(const HtaRequire &);
    void operator=(const HtaRequire &);
};


struct HtaOrder
{
    int      m_iOrder;
    uint64_t m_methods;
};


struct HtaEnvSet
{
    std::string m_sName;
    std::string m_sValue;
    bool        m_bUnset;
};


struct HtaExpr;


struct HtaEnvOp
{
    enum { SET, UNSET, MATCH, COND } m_type;
    std::string             m_sAttr;
    bool                    m_bNegate;
    Pcregex                *m_pRegex;
    std::vector<HtaEnvSet>  m_sets;
    std::string             m_sCond;    //only runs when this env is set
    HtaExpr                *m_pExpr;    //COND: NULL for <Else>
    std::vector<std::string> m_previous; //COND: earlier branches of the chain

    HtaEnvOp()
        : m_type(SET)
        , m_bNegate(false)
        , m_pRegex(NULL)
        , m_pExpr(NULL)
    {}
};


struct HtaRedirect
{
    int         m_iStatus;
    std::string m_sPath;
    std::string m_sTarget;
    Pcregex    *m_pRegex;
    std::string m_sCond;
};


static void releaseExpr(HtaExpr *pExpr);
bool parseAddr(const std::string &text, HtaAddr &addr);


class HtaRules
{
public:
    const HtaRules             *m_pParent;
    std::vector<HtaOrder>       m_orders;
    std::vector<HtaAddr>        m_allows;
    std::vector<HtaAddr>        m_denies;
    int                         m_iSatisfy;
    HtaRequire                 *m_pRequire;
    HTAuth                     *m_pAuth;
    AuthRequired               *m_pValidUser;
    std::string                 m_sAuthName;
    std::string                 m_sUserFile;
    std::string                 m_sGroupFile;
    bool                        m_bBasicAuth;
    bool                        m_bBroken;      //answer 500, like Apache does
    bool                        m_bDenyAll;     //a rule could not be understood
    std::vector<HtaEnvOp>       m_envOps;
    std::vector<HtaRedirect>    m_redirects;

    HtaRules()
        : m_pParent(NULL)
        , m_iSatisfy(SATISFY_UNSET)
        , m_pRequire(NULL)
        , m_pAuth(NULL)
        , m_pValidUser(NULL)
        , m_bBasicAuth(false)
        , m_bBroken(false)
        , m_bDenyAll(false)
    {}

    ~HtaRules()
    {
        delete m_pRequire;
        delete m_pAuth;
        delete m_pValidUser;
        for (size_t i = 0; i < m_envOps.size(); ++i)
        {
            delete m_envOps[i].m_pRegex;
            releaseExpr(m_envOps[i].m_pExpr);
        }
        for (size_t i = 0; i < m_redirects.size(); ++i)
            delete m_redirects[i].m_pRegex;
    }

    bool empty() const
    {
        return m_orders.empty() && m_allows.empty() && m_denies.empty()
               && m_iSatisfy == SATISFY_UNSET && !m_pRequire && !m_pAuth
               && !m_bBroken && !m_bDenyAll && m_envOps.empty()
               && m_redirects.empty();
    }

private:
    HtaRules(const HtaRules &);
    void operator=(const HtaRules &);
};


// ---------------------------------------------------------------------------
// <If> expressions: the part of the Apache syntax that real files use
// ---------------------------------------------------------------------------

struct HtaExpr
{
    enum Type
    {
        OR, AND, NOT, TRUE_, FALSE_, EQ, NE, LT, LE, GT, GE, NUM_EQ, NUM_NE,
        NUM_LT, NUM_LE, NUM_GT, NUM_GE, MATCH, NOMATCH, IPMATCH, NONEMPTY, EMPTY,
        VALUE, REQ_HEADER
    } m_type;
    HtaExpr        *m_pLeft;
    HtaExpr        *m_pRight;
    std::string     m_sText;
    Pcregex        *m_pRegex;
    HtaAddr         m_addr;

    explicit HtaExpr(Type type)
        : m_type(type)
        , m_pLeft(NULL)
        , m_pRight(NULL)
        , m_pRegex(NULL)
    {}

    ~HtaExpr()
    {
        delete m_pLeft;
        delete m_pRight;
        delete m_pRegex;
    }

private:
    HtaExpr(const HtaExpr &);
    void operator=(const HtaExpr &);
};


static void releaseExpr(HtaExpr *pExpr)
{
    delete pExpr;
}


class HtaExprParser
{
    const char *m_p;
    bool        m_bError;

    void skip()
    {
        while (isspace((unsigned char)*m_p))
            ++m_p;
    }

    bool accept(const char *pToken)
    {
        skip();
        size_t len = strlen(pToken);
        if (strncmp(m_p, pToken, len) != 0)
            return false;
        //A named operator must end where the word ends.
        if (isalpha((unsigned char)pToken[len - 1])
            && (isalnum((unsigned char)m_p[len]) || m_p[len] == '_'))
            return false;
        m_p += len;
        return true;
    }

    HtaExpr *fail(HtaExpr *pLeft = NULL)
    {
        delete pLeft;
        m_bError = true;
        return NULL;
    }

    HtaExpr *parseValue()
    {
        skip();
        if (*m_p == '\'' || *m_p == '"')
        {
            char quote = *m_p++;
            std::string text;
            while (*m_p && *m_p != quote)
            {
                if (*m_p == '\\' && m_p[1])
                    ++m_p;
                text += *m_p++;
            }
            if (!*m_p)
                return fail();
            ++m_p;
            HtaExpr *pValue = new HtaExpr(HtaExpr::VALUE);
            pValue->m_sText = text;
            return pValue;
        }
        if (*m_p == '%' && m_p[1] == '{')
        {
            const char *pEnd = strchr(m_p, '}');
            if (!pEnd)
                return fail();
            HtaExpr *pValue = new HtaExpr(HtaExpr::VALUE);
            pValue->m_sText.assign(m_p, pEnd + 1 - m_p);
            m_p = pEnd + 1;
            return pValue;
        }
        if (isdigit((unsigned char)*m_p))
        {
            const char *pBegin = m_p;
            while (isdigit((unsigned char)*m_p))
                ++m_p;
            HtaExpr *pValue = new HtaExpr(HtaExpr::VALUE);
            pValue->m_sText.assign(pBegin, m_p - pBegin);
            return pValue;
        }
        if (strncmp(m_p, "req(", 4) == 0 || strncmp(m_p, "http(", 5) == 0)
        {
            m_p = strchr(m_p, '(') + 1;
            HtaExpr *pName = parseValue();
            if (!pName || !accept(")"))
                return fail(pName);
            pName->m_type = HtaExpr::REQ_HEADER;
            return pName;
        }
        return fail();
    }

    HtaExpr *parseRegex(HtaExpr::Type type, HtaExpr *pLeft)
    {
        skip();
        char delim = *m_p;
        if (delim == 'm' && m_p[1])
            delim = *++m_p;
        if (delim != '/' && delim != '#' && delim != '!' && delim != '|')
            return fail(pLeft);
        ++m_p;
        std::string pattern;
        while (*m_p && *m_p != delim)
        {
            if (*m_p == '\\' && m_p[1] == delim)
                ++m_p;
            pattern += *m_p++;
        }
        if (!*m_p)
            return fail(pLeft);
        ++m_p;
        int options = 0;
        if (*m_p == 'i')
        {
            options = LSRE_CASELESS;
            ++m_p;
        }
        HtaExpr *pExpr = new HtaExpr(type);
        pExpr->m_pLeft = pLeft;
        pExpr->m_pRegex = new Pcregex();
        if (pExpr->m_pRegex->compile(pattern.c_str(), options) != 0)
            return fail(pExpr);
        return pExpr;
    }

    HtaExpr *parseComparison()
    {
        if (accept("true"))
            return new HtaExpr(HtaExpr::TRUE_);
        if (accept("false"))
            return new HtaExpr(HtaExpr::FALSE_);
        HtaExpr::Type unary = HtaExpr::TRUE_;
        if (accept("-n"))
            unary = HtaExpr::NONEMPTY;
        else if (accept("-z"))
            unary = HtaExpr::EMPTY;
        HtaExpr *pLeft = parseValue();
        if (!pLeft)
            return NULL;
        if (unary != HtaExpr::TRUE_)
        {
            HtaExpr *pExpr = new HtaExpr(unary);
            pExpr->m_pLeft = pLeft;
            return pExpr;
        }
        if (accept("=~"))
            return parseRegex(HtaExpr::MATCH, pLeft);
        if (accept("!~"))
            return parseRegex(HtaExpr::NOMATCH, pLeft);
        static const struct { const char *m_pOp; HtaExpr::Type m_type; } s_ops[] =
        {
            {"==", HtaExpr::EQ}, {"!=", HtaExpr::NE}, {"<=", HtaExpr::LE},
            {">=", HtaExpr::GE}, {"<", HtaExpr::LT}, {">", HtaExpr::GT},
            {"=", HtaExpr::EQ}, {"-eq", HtaExpr::NUM_EQ}, {"-ne", HtaExpr::NUM_NE},
            {"-lt", HtaExpr::NUM_LT}, {"-le", HtaExpr::NUM_LE},
            {"-gt", HtaExpr::NUM_GT}, {"-ge", HtaExpr::NUM_GE},
        };
        for (size_t i = 0; i < sizeof(s_ops) / sizeof(s_ops[0]); ++i)
        {
            if (accept(s_ops[i].m_pOp))
            {
                HtaExpr *pRight = parseValue();
                if (!pRight)
                    return fail(pLeft);
                HtaExpr *pExpr = new HtaExpr(s_ops[i].m_type);
                pExpr->m_pLeft = pLeft;
                pExpr->m_pRight = pRight;
                return pExpr;
            }
        }
        if (accept("-ipmatch"))
        {
            HtaExpr *pRight = parseValue();
            if (!pRight)
                return fail(pLeft);
            HtaExpr *pExpr = new HtaExpr(HtaExpr::IPMATCH);
            pExpr->m_pLeft = pLeft;
            bool ok = parseAddr(pRight->m_sText, pExpr->m_addr);
            delete pRight;
            if (!ok)
                return fail(pExpr);
            return pExpr;
        }
        return fail(pLeft);
    }

    HtaExpr *parseUnary()
    {
        if (accept("!"))
        {
            HtaExpr *pInner = parseUnary();
            if (!pInner)
                return NULL;
            HtaExpr *pExpr = new HtaExpr(HtaExpr::NOT);
            pExpr->m_pLeft = pInner;
            return pExpr;
        }
        if (accept("("))
        {
            HtaExpr *pInner = parseOr();
            if (!pInner || !accept(")"))
                return fail(pInner);
            return pInner;
        }
        return parseComparison();
    }

    HtaExpr *parseBinary(HtaExpr::Type type, const char *pOp,
                         HtaExpr *(HtaExprParser::*pNext)())
    {
        HtaExpr *pLeft = (this->*pNext)();
        while (pLeft && accept(pOp))
        {
            HtaExpr *pRight = (this->*pNext)();
            if (!pRight)
                return fail(pLeft);
            HtaExpr *pExpr = new HtaExpr(type);
            pExpr->m_pLeft = pLeft;
            pExpr->m_pRight = pRight;
            pLeft = pExpr;
        }
        return pLeft;
    }

    HtaExpr *parseAnd()
    {   return parseBinary(HtaExpr::AND, "&&", &HtaExprParser::parseUnary); }

    HtaExpr *parseOr()
    {   return parseBinary(HtaExpr::OR, "||", &HtaExprParser::parseAnd);    }

public:
    explicit HtaExprParser(const char *pText)
        : m_p(pText)
        , m_bError(false)
    {}

    HtaExpr *parse()
    {
        HtaExpr *pExpr = parseOr();
        skip();
        if (m_bError || *m_p)
        {
            delete pExpr;
            return NULL;
        }
        return pExpr;
    }
};


void Htaccess::releaseRules(HtaRules *pRules)
{
    delete pRules;
}


void Htaccess::setParentRules(HtaRules *pRules, const HtaRules *pParent)
{
    pRules->m_pParent = pParent;
}


// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

/** Splits a line the way Apache does: on blanks, keeping quoted text together. */
static void splitArgs(const char *p, const char *pEnd,
                      std::vector<std::string> &args)
{
    while (p < pEnd)
    {
        while (p < pEnd && isspace((unsigned char)*p))
            ++p;
        if (p >= pEnd)
            break;
        std::string arg;
        if (*p == '"' || *p == '\'')
        {
            char quote = *p++;
            while (p < pEnd && *p != quote)
            {
                if (*p == '\\' && p + 1 < pEnd
                    && (p[1] == quote || p[1] == '\\'))
                    ++p;
                arg += *p++;
            }
            if (p < pEnd)
                ++p;
        }
        else
        {
            while (p < pEnd && !isspace((unsigned char)*p))
                arg += *p++;
        }
        args.push_back(arg);
    }
}


int Htaccess::parse(const char *pText, std::vector<HtaDirective> &directives)
{
    std::vector<HtaDirective> stack;
    stack.push_back(HtaDirective());
    int lineNo = 0;
    const char *p = pText;
    while (*p)
    {
        //Join the lines ended by a backslash.
        std::string line;
        int firstLine = lineNo + 1;
        while (*p)
        {
            const char *pEol = strchr(p, '\n');
            if (!pEol)
                pEol = p + strlen(p);
            ++lineNo;
            const char *pEnd = pEol;
            if (pEnd > p && pEnd[-1] == '\r')
                --pEnd;
            bool cont = (pEnd > p && pEnd[-1] == '\\' && *pEol);
            line.append(p, cont ? pEnd - 1 - p : pEnd - p);
            p = *pEol ? pEol + 1 : pEol;
            if (!cont)
                break;
        }

        const char *pBegin = line.c_str();
        const char *pEnd = pBegin + line.size();
        while (pBegin < pEnd && isspace((unsigned char)*pBegin))
            ++pBegin;
        while (pEnd > pBegin && isspace((unsigned char)pEnd[-1]))
            --pEnd;
        if (pBegin == pEnd || *pBegin == '#')
            continue;

        if (*pBegin == '<' && pBegin[1] == '/')
        {
            std::string name(pBegin + 2, pEnd - pBegin - 2);
            if (!name.empty() && name[name.size() - 1] == '>')
                name.erase(name.size() - 1);
            if (stack.size() < 2
                || strcasecmp(stack.back().m_sName.c_str(), name.c_str()) != 0)
                return firstLine;
            HtaDirective block = stack.back();
            stack.pop_back();
            stack.back().m_children.push_back(block);
            continue;
        }

        HtaDirective dir;
        dir.m_iLine = firstLine;
        dir.m_sLine.assign(pBegin, pEnd - pBegin);
        if (*pBegin == '<')
        {
            if (pEnd[-1] != '>' || stack.size() > HTA_MAX_DEPTH)
                return firstLine;
            --pEnd;
            ++pBegin;
            dir.m_bBlock = true;
        }
        const char *pName = pBegin;
        while (pBegin < pEnd && !isspace((unsigned char)*pBegin))
            ++pBegin;
        dir.m_sName.assign(pName, pBegin - pName);
        splitArgs(pBegin, pEnd, dir.m_args);
        if (dir.m_bBlock)
            stack.push_back(dir);
        else
            stack.back().m_children.push_back(dir);
    }
    if (stack.size() != 1)
        return lineNo ? lineNo : 1;
    directives.swap(stack[0].m_children);
    return 0;
}


// ---------------------------------------------------------------------------
// Applying the directives
// ---------------------------------------------------------------------------

struct HtaBuild
{
    HttpVHost      *m_pVHost;
    const char     *m_pFile;
    PHPConfig      *m_pPHP;
    std::string     m_sRewrite;
    std::string     m_sCond;    //set inside <If>
    std::vector<std::string> m_chain;   //branches of the current <If> chain
};


static void warn(const HtaBuild &b, const HtaDirective &dir, const char *pMsg)
{
    LS_WARN("[%s] %s:%d: %s: %s", b.m_pVHost->getName(), b.m_pFile, dir.m_iLine,
            pMsg, dir.m_sLine.c_str());
}


static bool isOn(const std::string &s)
{
    return strcasecmp(s.c_str(), "on") == 0;
}


static int methodIndex(const char *pName)
{
    for (int i = 1; i < HttpMethod::HTTP_METHOD_END && i < 64; ++i)
    {
        const char *p = HttpMethod::get((http_method_t)i);
        if (p && strcasecmp(p, pName) == 0)
            return i;
    }
    return 0;
}


static uint64_t parseMethods(const std::vector<std::string> &args, bool invert)
{
    uint64_t mask = 0;
    for (size_t i = 0; i < args.size(); ++i)
    {
        int bit = methodIndex(args[i].c_str());
        if (bit)
            mask |= ((uint64_t)1 << bit);
    }
    //Apache treats GET as also covering HEAD.
    if (mask & ((uint64_t)1 << HttpMethod::HTTP_GET))
        mask |= ((uint64_t)1 << HttpMethod::HTTP_HEAD);
    return invert ? ~mask : mask;
}


bool parseAddr(const std::string &text, HtaAddr &addr)
{
    addr.m_type = HtaAddr::NET;
    std::string host = text;
    int bits = -1;
    size_t slash = text.find('/');
    std::string mask;
    if (slash != std::string::npos)
    {
        host = text.substr(0, slash);
        mask = text.substr(slash + 1);
    }

    bool v6 = host.find(':') != std::string::npos;
    unsigned char *pAddr = addr.m_addr;
    if (v6)
    {
        if (inet_pton(AF_INET6, host.c_str(), pAddr) != 1)
            return false;
    }
    else
    {
        //A partial address such as 10.1 means the whole 10.1.0.0/16 network.
        int octets[4] = {0, 0, 0, 0};
        int count = 0;
        const char *p = host.c_str();
        while (*p && count < 4)
        {
            if (!isdigit((unsigned char)*p))
                return false;
            octets[count] = strtol(p, (char **)&p, 10);
            if (octets[count++] > 255)
                return false;
            if (*p == '.')
                ++p;
            else if (*p)
                return false;
        }
        if (!count || *p)
            return false;
        if (mask.empty())
            bits = count * 8;
        //Stored mapped into IPv6, the same way peers are compared.
        memset(pAddr, 0, 10);
        pAddr[10] = pAddr[11] = 0xff;
        for (int i = 0; i < 4; ++i)
            pAddr[12 + i] = octets[i];
    }

    if (!mask.empty())
    {
        if (mask.find('.') != std::string::npos)
        {
            in_addr netmask;
            if (v6 || inet_pton(AF_INET, mask.c_str(), &netmask) != 1)
                return false;
            uint32_t m = ntohl(netmask.s_addr);
            bits = 0;
            while (m & 0x80000000u)
            {
                ++bits;
                m <<= 1;
            }
        }
        else
        {
            char *pEnd;
            bits = strtol(mask.c_str(), &pEnd, 10);
            if (*pEnd || bits < 0 || bits > (v6 ? 128 : 32))
                return false;
        }
    }
    else if (bits == -1)
        bits = v6 ? 128 : 32;
    addr.m_iBits = v6 ? bits : bits + 96;
    return true;
}


static bool isHostName(const std::string &s)
{
    for (size_t i = 0; i < s.size(); ++i)
        if (isalpha((unsigned char)s[i]))
            return s.find(':') == std::string::npos;
    return false;
}


static bool parseFrom(const std::string &text, uint64_t methods, HtaAddr &addr)
{
    addr.m_methods = methods;
    if (strcasecmp(text.c_str(), "all") == 0)
    {
        addr.m_type = HtaAddr::ALL;
        return true;
    }
    if (strncasecmp(text.c_str(), "env=", 4) == 0)
    {
        const char *p = text.c_str() + 4;
        addr.m_type = HtaAddr::ENV;
        addr.m_bNegate = (*p == '!');
        addr.m_sEnv = p + addr.m_bNegate;
        return true;
    }
    if (isHostName(text))
    {
        addr.m_type = HtaAddr::HOST;
        addr.m_sEnv = text;
        return true;
    }
    return parseAddr(text, addr);
}


static bool addAllowDeny(const HtaBuild &b, HtaRules *pRules,
                         const HtaDirective &dir, uint64_t methods, bool deny)
{
    size_t i = 0;
    if (!dir.m_args.empty() && strcasecmp(dir.m_args[0].c_str(), "from") == 0)
        i = 1;
    if (i >= dir.m_args.size())
    {
        warn(b, dir, "missing address");
        return false;
    }
    std::vector<HtaAddr> &list = deny ? pRules->m_denies : pRules->m_allows;
    for (; i < dir.m_args.size(); ++i)
    {
        HtaAddr addr;
        if (!parseFrom(dir.m_args[i], methods, addr))
        {
            warn(b, dir, "invalid address");
            return false;
        }
        list.push_back(addr);
    }
    return true;
}


static HtaRequire *parseRequire(const HtaBuild &b, const HtaDirective &dir,
                                uint64_t methods)
{
    size_t i = 0;
    bool negate = false;
    if (!dir.m_args.empty() && strcasecmp(dir.m_args[0].c_str(), "not") == 0)
    {
        negate = true;
        i = 1;
    }
    if (i >= dir.m_args.size())
        return NULL;
    const char *pType = dir.m_args[i++].c_str();

    HtaRequire *pReq = NULL;
    if (strcasecmp(pType, "all") == 0)
    {
        if (i >= dir.m_args.size())
            return NULL;
        const char *pValue = dir.m_args[i].c_str();
        if (strcasecmp(pValue, "granted") == 0)
            pReq = new HtaRequire(HtaRequire::ALL_GRANTED);
        else if (strcasecmp(pValue, "denied") == 0)
            pReq = new HtaRequire(HtaRequire::ALL_DENIED);
        else
            return NULL;
    }
    else if (strcasecmp(pType, "ip") == 0)
    {
        pReq = new HtaRequire(HtaRequire::IP);
        for (; i < dir.m_args.size(); ++i)
        {
            HtaAddr addr;
            if (!parseAddr(dir.m_args[i], addr))
            {
                delete pReq;
                return NULL;
            }
            pReq->m_addrs.push_back(addr);
        }
    }
    else if (strcasecmp(pType, "local") == 0)
    {
        pReq = new HtaRequire(HtaRequire::IP);
        HtaAddr v4, v6;
        parseAddr("127.0.0.0/8", v4);
        parseAddr("::1", v6);
        pReq->m_addrs.push_back(v4);
        pReq->m_addrs.push_back(v6);
    }
    else
    {
        HtaRequire::Type type;
        if (strcasecmp(pType, "valid-user") == 0)
            type = HtaRequire::VALID_USER;
        else if (strcasecmp(pType, "user") == 0)
            type = HtaRequire::USER;
        else if (strcasecmp(pType, "group") == 0)
            type = HtaRequire::GROUP;
        else if (strcasecmp(pType, "host") == 0)
            type = HtaRequire::HOST;
        else if (strcasecmp(pType, "env") == 0)
            type = HtaRequire::ENV;
        else if (strcasecmp(pType, "method") == 0)
            type = HtaRequire::METHOD;
        else
            return NULL;
        pReq = new HtaRequire(type);
        pReq->m_args.assign(dir.m_args.begin() + i, dir.m_args.end());
        if (type == HtaRequire::GROUP)
        {
            std::string required = "group";
            for (size_t n = 0; n < pReq->m_args.size(); ++n)
                required += " " + pReq->m_args[n];
            pReq->m_pGroups = new AuthRequired();
            pReq->m_pGroups->parse(required.c_str());
        }
    }
    pReq->m_bNegate = negate;
    pReq->m_methods = methods;
    return pReq;
}


static bool parseRequireBlock(const HtaBuild &b, const HtaDirective &block,
                              HtaRequire *pParent, uint64_t methods);


/** Adds a Require line or a nested Require block to pParent. */
static bool addRequire(const HtaBuild &b, const HtaDirective &dir,
                       HtaRequire *pParent, uint64_t methods)
{
    const char *pName = dir.m_sName.c_str();
    HtaRequire *pReq = NULL;
    if (dir.m_bBlock)
    {
        HtaRequire::Type type;
        if (strcasecmp(pName, "RequireAny") == 0)
            type = HtaRequire::ANY;
        else if (strcasecmp(pName, "RequireAll") == 0)
            type = HtaRequire::ALL_OF;
        else if (strcasecmp(pName, "RequireNone") == 0)
            type = HtaRequire::NONE;
        else
            return false;
        pReq = new HtaRequire(type);
        pReq->m_sCond = b.m_sCond;
        pParent->m_children.push_back(pReq);
        return parseRequireBlock(b, dir, pReq, methods);
    }
    if (strcasecmp(pName, "Require") != 0)
        return false;
    pReq = parseRequire(b, dir, methods);
    if (!pReq)
        return false;
    pReq->m_sCond = b.m_sCond;
    pParent->m_children.push_back(pReq);
    return true;
}


static bool parseRequireBlock(const HtaBuild &b, const HtaDirective &block,
                              HtaRequire *pParent, uint64_t methods)
{
    for (size_t i = 0; i < block.m_children.size(); ++i)
        if (!addRequire(b, block.m_children[i], pParent, methods))
            return false;
    return true;
}


static const char *const s_modules[] =
{
    "access_compat", "actions", "alias", "auth_basic", "authn_core", "authn_file",
    "authz_core", "authz_groupfile", "authz_host", "authz_user", "autoindex",
    "core", "deflate", "dir", "env", "expires", "filter", "headers", "include",
    "litespeed", "mime", "negotiation", "php", "php5", "php7", "php8", "rewrite",
    "setenvif", "so", "ssl", "unixd", "version",
    NULL
};


/** Accepts mod_rewrite.c, mod_rewrite and rewrite_module. */
static bool hasModule(const std::string &arg)
{
    std::string name = arg;
    for (size_t i = 0; i < name.size(); ++i)
        name[i] = tolower((unsigned char)name[i]);
    if (name.compare(0, 4, "mod_") == 0)
        name.erase(0, 4);
    size_t len = name.size();
    if (len > 2 && name.compare(len - 2, 2, ".c") == 0)
        name.erase(len - 2);
    else if (len > 7 && name.compare(len - 7, 7, "_module") == 0)
        name.erase(len - 7);
    for (int i = 0; s_modules[i]; ++i)
        if (name == s_modules[i])
            return true;
    return false;
}


static bool versionMatches(const std::vector<std::string> &args)
{
    if (args.empty())
        return false;
    std::string op = "=";
    std::string ver = args[0];
    if (args.size() > 1)
    {
        op = args[0];
        ver = args[1];
    }
    else
    {
        size_t digit = ver.find_first_of("0123456789");
        if (digit != std::string::npos && digit > 0)
        {
            op = ver.substr(0, digit);
            ver.erase(0, digit);
        }
    }
    int major = 0, minor = 0, patch = 0;
    int fields = sscanf(ver.c_str(), "%d.%d.%d", &major, &minor, &patch);
    int cur[3] = {HTA_VER_MAJOR, HTA_VER_MINOR, HTA_VER_PATCH};
    int req[3] = {major, minor, patch};
    int cmp = 0;
    for (int i = 0; i < 3 && i < fields && !cmp; ++i)
        cmp = cur[i] - req[i];
    if (op == "=" || op == "==")
        return cmp == 0;
    if (op == ">")
        return cmp > 0;
    if (op == ">=")
        return cmp >= 0;
    if (op == "<")
        return cmp < 0;
    if (op == "<=")
        return cmp <= 0;
    return op == "!=" && cmp != 0;
}


/** Engine flags that a site owner must not control, as in RewriteRule [E=]. */
static bool reservedEnv(const std::string &name)
{
    return strcasecmp(name.c_str(), "modsecurity") == 0;
}


static bool parseEnvSets(const std::vector<std::string> &args, size_t first,
                         HtaEnvOp &op)
{
    for (size_t i = first; i < args.size(); ++i)
    {
        HtaEnvSet set;
        const char *p = args[i].c_str();
        set.m_bUnset = (*p == '!');
        if (set.m_bUnset)
            ++p;
        const char *pEq = strchr(p, '=');
        if (pEq)
        {
            set.m_sName.assign(p, pEq - p);
            set.m_sValue = pEq + 1;
        }
        else
        {
            set.m_sName = p;
            set.m_sValue = "1";
        }
        if (set.m_sName.empty() || reservedEnv(set.m_sName))
            return false;
        op.m_sets.push_back(set);
    }
    return !op.m_sets.empty();
}


static bool addEnvMatch(const HtaBuild &b, const HtaDirective &dir,
                        HtaRules *pRules, const char *pAttr, size_t regexArg,
                        bool noCase)
{
    if (dir.m_args.size() <= regexArg + 1)
        return false;
    HtaEnvOp op;
    op.m_sCond = b.m_sCond;
    op.m_type = HtaEnvOp::MATCH;
    op.m_sAttr = pAttr;
    op.m_pRegex = new Pcregex();
    if (op.m_pRegex->compile(dir.m_args[regexArg].c_str(),
                             noCase ? LSRE_CASELESS : 0) != 0
        || !parseEnvSets(dir.m_args, regexArg + 1, op))
    {
        delete op.m_pRegex;
        return false;
    }
    pRules->m_envOps.push_back(op);
    return true;
}


static int redirectStatus(const char *pText)
{
    if (strcasecmp(pText, "permanent") == 0)
        return SC_301;
    if (strcasecmp(pText, "temp") == 0)
        return SC_302;
    if (strcasecmp(pText, "seeother") == 0)
        return SC_303;
    if (strcasecmp(pText, "gone") == 0)
        return SC_410;
    if (isdigit((unsigned char)*pText))
    {
        int code = HttpStatusCode::getInstance().codeToIndex(pText);
        return code > 0 ? code : 0;
    }
    return 0;
}


static bool addRedirect(const HtaBuild &b, const HtaDirective &dir,
                        HtaRules *pRules, int forced, bool regex)
{
    const std::vector<std::string> &args = dir.m_args;
    size_t i = 0;
    HtaRedirect redirect;
    redirect.m_sCond = b.m_sCond;
    redirect.m_iStatus = forced ? forced : SC_302;
    redirect.m_pRegex = NULL;
    if (!forced && args.size() > 1)
    {
        int status = redirectStatus(args[0].c_str());
        if (status)
        {
            redirect.m_iStatus = status;
            i = 1;
        }
    }
    if (i >= args.size())
        return false;
    redirect.m_sPath = args[i++];
    if (i < args.size())
        redirect.m_sTarget = args[i];
    else if (redirect.m_iStatus != SC_410)
        return false;
    if (regex)
    {
        redirect.m_pRegex = new Pcregex();
        if (redirect.m_pRegex->compile(redirect.m_sPath.c_str(), 0) != 0)
        {
            delete redirect.m_pRegex;
            return false;
        }
    }
    pRules->m_redirects.push_back(redirect);
    return true;
}


/** Password files must stay inside the virtual host. */
static bool allowedAuthFile(HttpVHost *pVHost, const std::string &path)
{
    const char *pRoot = pVHost->getVhRoot()->c_str();
    return !path.empty() && path[0] == '/'
           && path.find("/../") == std::string::npos
           && strncmp(path.c_str(), pRoot, strlen(pRoot)) == 0;
}


static void finishAuth(const HtaBuild &b, HtaRules *pRules)
{
    if (!pRules->m_bBasicAuth || pRules->m_sUserFile.empty())
        return;
    if (!allowedAuthFile(b.m_pVHost, pRules->m_sUserFile)
        || (!pRules->m_sGroupFile.empty()
            && !allowedAuthFile(b.m_pVHost, pRules->m_sGroupFile)))
    {
        LS_WARN("[%s] %s: password file outside the virtual host, denying access.",
                b.m_pVHost->getName(), b.m_pFile);
        pRules->m_bDenyAll = true;
        return;
    }

    std::string realm = "hta:" + pRules->m_sUserFile + "|" + pRules->m_sGroupFile;
    UserDir *pDir = b.m_pVHost->getRealm(realm.c_str());
    if (!pDir)
        pDir = b.m_pVHost->getFileUserDir(realm.c_str(),
                pRules->m_sUserFile.c_str(),
                pRules->m_sGroupFile.empty() ? NULL : pRules->m_sGroupFile.c_str());
    if (!pDir)
    {
        pRules->m_bDenyAll = true;
        return;
    }
    pRules->m_pAuth = new HTAuth();
    std::string name = pRules->m_sAuthName.substr(0, HTAuth::MAX_REALM_NAME_LEN - 1);
    pRules->m_pAuth->setName(name.empty() ? "Restricted" : name.c_str());
    pRules->m_pAuth->setUserDir(pDir);
    pRules->m_pAuth->setAuthType(HTAuth::AUTH_BASIC);
    pRules->m_pValidUser = new AuthRequired();
    pRules->m_pValidUser->parse("valid-user");
}


static HtaRules *ownRules(HttpContext *pContext);
static void applyList(HtaBuild &b, HttpContext *pContext,
                      const std::vector<HtaDirective> &list, uint64_t methods);
static void applyOne(HtaBuild &b, HttpContext *pContext, const HtaDirective &dir,
                     uint64_t methods);


static void applyFiles(HtaBuild &b, HttpContext *pContext,
                       const HtaDirective &block, bool regex)
{
    std::string pattern = block.m_args.empty() ? "" : block.m_args[0];
    //<Files ~ "regex"> is the old spelling of <FilesMatch>.
    if (!regex && block.m_args.size() > 1 && pattern == "~")
    {
        pattern = block.m_args[1];
        regex = true;
    }
    if (pattern.empty())
    {
        warn(b, block, "missing file pattern");
        return;
    }

    HttpContext *pFiles = new HttpContext();
    if (pFiles->setFilesMatch(pattern.c_str(), regex) != 0)
    {
        warn(b, block, "invalid file pattern");
        delete pFiles;
        if (!strcasecmp(block.m_sName.c_str(), "Files")
            || !strcasecmp(block.m_sName.c_str(), "FilesMatch"))
            ownRules(pContext)->m_bDenyAll = true;
        return;
    }
    pContext->addFilesMatchContext(pFiles);
    //The rewrite text belongs to the directory, not to this block.
    std::string saved;
    saved.swap(b.m_sRewrite);
    applyList(b, pFiles, block.m_children, HTA_ALL_METHODS);
    b.m_sRewrite.swap(saved);
}


static bool isIgnored(const char *pName)
{
    //Handled by the server settings, or meaningless here.
    static const char *const s_ignored[] =
    {
        "AddOutputFilter", "AddOutputFilterByType", "AddEncoding", "AddCharset",
        "AddLanguage", "AddIcon", "AddIconByType", "AddIconByEncoding",
        "AddDescription", "DefaultIcon", "FancyIndexing", "HeaderName",
        "IndexIgnore", "IndexOptions", "IndexOrderDefault", "ReadmeName",
        "RemoveOutputFilter", "SetOutputFilter", "SetInputFilter",
        "DeflateCompressionLevel", "ServerSignature",
        "LimitRequestBody", "CheckSpelling", "CGIPassAuth", "FileETag",
        "AuthBasicProvider", "AuthMerging",
        "RemoveHandler", "RemoveType", "XBitHack", "DirectorySlash",
        "RewriteOptions", "ContentDigest", "EnableMMAP", "EnableSendfile",
        NULL
    };
    for (int i = 0; s_ignored[i]; ++i)
        if (strcasecmp(pName, s_ignored[i]) == 0)
            return true;
    return false;
}


/** Panels write versioned names, such as application/x-httpd-ea-php83. */
static bool isPhpType(const char *pType)
{
    return strcasestr(pType, "php") != NULL
           && strncasecmp(pType, "text/", 5) != 0;
}


static void addSuffixes(HttpContext *pContext, const char *pType,
                        const std::vector<std::string> &args)
{
    for (size_t i = 1; i < args.size(); ++i)
    {
        const char *pSuffix = args[i].c_str();
        if (*pSuffix == '.')
            ++pSuffix;
        pContext->addMIME(pType, pSuffix);
    }
}


static void forceType(HtaBuild &b, HttpContext *pContext, const char *pType)
{
    //A type missing from mime.properties has to exist before it can be forced.
    std::string type = pType;
    if (strcasecmp(pType, "none") != 0)
        pContext->addMIME(pType, "ls_hta_forcetype");
    pContext->setForceType(&type[0], b.m_pVHost->getName());
}


static void applyOptions(HttpContext *pContext, const HtaDirective &dir)
{
    for (size_t i = 0; i < dir.m_args.size(); ++i)
    {
        const char *p = dir.m_args[i].c_str();
        int enable = (*p != '-');
        if (*p == '+' || *p == '-')
            ++p;
        if (strcasecmp(p, "Indexes") == 0)
            pContext->setAutoIndex(enable);
        else if (strcasecmp(p, "All") == 0)
            pContext->setAutoIndex(1);
        else if (strcasecmp(p, "None") == 0)
            pContext->setAutoIndex(0);
    }
}


static bool applyAccess(HtaBuild &b, HttpContext *pContext,
                        const HtaDirective &dir, uint64_t methods, bool &ok)
{
    const char *pName = dir.m_sName.c_str();
    bool isRequireBlock = dir.m_bBlock
                          && strncasecmp(pName, "Require", 7) == 0;
    if (isRequireBlock || strcasecmp(pName, "Require") == 0)
    {
        HtaRules *pRules = ownRules(pContext);
        if (!pRules->m_pRequire)
            pRules->m_pRequire = new HtaRequire(HtaRequire::ANY);
        ok = addRequire(b, dir, pRules->m_pRequire, methods);
        return true;
    }
    if (strcasecmp(pName, "Order") == 0)
    {
        std::string value;
        for (size_t i = 0; i < dir.m_args.size(); ++i)
            value += dir.m_args[i];
        HtaOrder order;
        order.m_methods = methods;
        if (strcasecmp(value.c_str(), "deny,allow") == 0)
            order.m_iOrder = ORDER_DENY_ALLOW;
        else if (strcasecmp(value.c_str(), "allow,deny") == 0)
            order.m_iOrder = ORDER_ALLOW_DENY;
        else if (strcasecmp(value.c_str(), "mutual-failure") == 0)
            order.m_iOrder = ORDER_MUTUAL;
        else
        {
            ok = false;
            return true;
        }
        ownRules(pContext)->m_orders.push_back(order);
        return true;
    }
    if (strcasecmp(pName, "Allow") == 0 || strcasecmp(pName, "Deny") == 0)
    {
        ok = addAllowDeny(b, ownRules(pContext), dir, methods,
                          strcasecmp(pName, "Deny") == 0);
        return true;
    }
    if (strcasecmp(pName, "Satisfy") == 0)
    {
        if (dir.m_args.empty())
            ok = false;
        else
            ownRules(pContext)->m_iSatisfy =
                strcasecmp(dir.m_args[0].c_str(), "any") == 0
                ? SATISFY_ANY : SATISFY_ALL;
        return true;
    }
    if (strcasecmp(pName, "AuthType") == 0)
    {
        ownRules(pContext)->m_bBasicAuth = !dir.m_args.empty()
                && strcasecmp(dir.m_args[0].c_str(), "Basic") == 0;
        return true;
    }
    if (strcasecmp(pName, "AuthName") == 0)
    {
        if (!dir.m_args.empty())
            ownRules(pContext)->m_sAuthName = dir.m_args[0];
        return true;
    }
    if (strcasecmp(pName, "AuthUserFile") == 0)
    {
        if (!dir.m_args.empty())
            ownRules(pContext)->m_sUserFile = dir.m_args[0];
        return true;
    }
    if (strcasecmp(pName, "AuthGroupFile") == 0)
    {
        if (!dir.m_args.empty())
            ownRules(pContext)->m_sGroupFile = dir.m_args[0];
        return true;
    }
    return false;
}


/** Directives that can run conditionally at request time. */
static bool allowedInIf(const char *pName)
{
    static const char *const s_allowed[] =
    {
        "Require", "Header", "SetEnv", "UnsetEnv", "SetEnvIf", "SetEnvIfNoCase",
        "BrowserMatch", "BrowserMatchNoCase", "Redirect", "RedirectMatch",
        "RedirectPermanent", "RedirectTemp", NULL
    };
    for (int i = 0; s_allowed[i]; ++i)
        if (strcasecmp(pName, s_allowed[i]) == 0)
            return true;
    return false;
}


/**
 * A block turns into an environment variable set early in the request, and the
 * directives inside it depend on that variable. This reuses env= on headers.
 */
static void applyIf(HtaBuild &b, HttpContext *pContext, const HtaDirective &block)
{
    static int s_iCondSeq = 0;
    const char *pName = block.m_sName.c_str();
    bool isElse = strcasecmp(pName, "Else") == 0;
    bool isIf = strcasecmp(pName, "If") == 0;
    if (isIf)
        b.m_chain.clear();
    else if (b.m_chain.empty())
    {
        warn(b, block, "no matching <If>, block ignored");
        return;
    }

    HtaEnvOp op;
    op.m_type = HtaEnvOp::COND;
    op.m_sCond = b.m_sCond;
    op.m_previous = b.m_chain;
    if (!isElse)
    {
        HtaExprParser parser(block.m_args.empty() ? "" : block.m_args[0].c_str());
        op.m_pExpr = parser.parse();
        if (!op.m_pExpr)
        {
            warn(b, block, "invalid expression, denying access");
            ownRules(pContext)->m_bDenyAll = true;
            return;
        }
    }
    char achName[40];
    snprintf(achName, sizeof(achName), "LS_HTA_IF_%d", ++s_iCondSeq);
    op.m_sAttr = achName;
    ownRules(pContext)->m_envOps.push_back(op);
    b.m_chain.push_back(achName);

    std::string saved = b.m_sCond;
    std::vector<std::string> savedChain;
    savedChain.swap(b.m_chain);
    b.m_sCond = achName;
    for (size_t i = 0; i < block.m_children.size(); ++i)
    {
        const HtaDirective &dir = block.m_children[i];
        const char *pChild = dir.m_sName.c_str();
        if (allowedInIf(pChild) || strncasecmp(pChild, "Require", 7) == 0
            || strcasecmp(pChild, "If") == 0 || strcasecmp(pChild, "ElseIf") == 0
            || strcasecmp(pChild, "Else") == 0)
            applyOne(b, pContext, dir, HTA_ALL_METHODS);
        else
            warn(b, dir, "not supported inside <If>, ignored");
    }
    b.m_sCond = saved;
    b.m_chain.swap(savedChain);
    if (isElse)
        b.m_chain.clear();
}


static void applyOne(HtaBuild &b, HttpContext *pContext, const HtaDirective &dir,
                     uint64_t methods)
{
    const char *pName = dir.m_sName.c_str();
    const std::vector<std::string> &args = dir.m_args;
    const char *pArg = args.empty() ? "" : args[0].c_str();

    if (dir.m_bBlock)
    {
        bool accessBlock = strncasecmp(pName, "Require", 7) == 0;
        if (strcasecmp(pName, "If") == 0 || strcasecmp(pName, "ElseIf") == 0
            || strcasecmp(pName, "Else") == 0)
            applyIf(b, pContext, dir);
        else if (strcasecmp(pName, "IfModule") == 0)
        {
            bool negate = *pArg == '!';
            if (hasModule(pArg + negate) != negate)
                applyList(b, pContext, dir.m_children, methods);
        }
        else if (strcasecmp(pName, "IfDefine") == 0)
        {
            if (*pArg == '!')
                applyList(b, pContext, dir.m_children, methods);
        }
        else if (strcasecmp(pName, "IfVersion") == 0)
        {
            if (versionMatches(args))
                applyList(b, pContext, dir.m_children, methods);
        }
        else if (strcasecmp(pName, "Files") == 0)
            applyFiles(b, pContext, dir, false);
        else if (strcasecmp(pName, "FilesMatch") == 0)
            applyFiles(b, pContext, dir, true);
        else if (strcasecmp(pName, "Limit") == 0)
            applyList(b, pContext, dir.m_children, parseMethods(args, false));
        else if (strcasecmp(pName, "LimitExcept") == 0)
            applyList(b, pContext, dir.m_children, parseMethods(args, true));
        else if (accessBlock)
        {
            bool ok = true;
            applyAccess(b, pContext, dir, methods, ok);
            if (!ok)
            {
                warn(b, dir, "invalid access rule, denying access");
                ownRules(pContext)->m_bDenyAll = true;
            }
        }
        else
        {
            warn(b, dir, "unsupported block ignored");
            if (b.m_pVHost->getHtaMode() == HttpVHost::HTA_STRICT)
                ownRules(pContext)->m_bBroken = true;
        }
        return;
    }

    bool ok = true;
    if (applyAccess(b, pContext, dir, methods, ok))
    {
        if (!ok)
        {
            warn(b, dir, "invalid access rule, denying access");
            ownRules(pContext)->m_bDenyAll = true;
        }
    }
    else if (strncasecmp(pName, "Rewrite", 7) == 0
             && strcasecmp(pName, "RewriteOptions") != 0)
    {
        b.m_sRewrite += dir.m_sLine;
        b.m_sRewrite += '\n';
    }
    else if (strcasecmp(pName, "Header") == 0
             || strcasecmp(pName, "RequestHeader") == 0)
    {
        std::string line = dir.m_sLine;
        if (!b.m_sCond.empty())
            line += " env=" + b.m_sCond;
        pContext->setHeaderOps(b.m_pVHost->getName(), line.c_str(), line.size(), 1);
    }
    else if (strcasecmp(pName, "ExpiresActive") == 0)
    {
        pContext->getExpires().enable(isOn(pArg));
        pContext->setConfigBit(BIT_ENABLE_EXPIRES, 1);
    }
    else if (strcasecmp(pName, "ExpiresDefault") == 0)
    {
        if (pContext->getExpires().parse(pArg) == 0)
            pContext->setConfigBit(BIT_EXPIRES_DEFAULT, 1);
        else
            warn(b, dir, "invalid expiry");
    }
    else if (strcasecmp(pName, "ExpiresByType") == 0)
    {
        if (args.size() < 2)
            warn(b, dir, "invalid expiry");
        else
        {
            std::string value = args[0] + "=" + args[1];
            pContext->setExpiresByType(value.c_str());
        }
    }
    else if (strcasecmp(pName, "ErrorDocument") == 0)
    {
        if (args.size() < 2)
            warn(b, dir, "invalid error document");
        else
        {
            //Quoted text is told apart from a URL by the quote kept in front.
            std::string value = args[1];
            if (value[0] != '/' && value.compare(0, 4, "http") != 0)
                value = "\"" + value;
            pContext->setCustomErrUrls(args[0].c_str(), value.c_str());
        }
    }
    else if (strcasecmp(pName, "DirectoryIndex") == 0)
    {
        std::string list;
        for (size_t i = 0; i < args.size(); ++i)
        {
            if (i)
                list += ',';
            list += args[i];
        }
        pContext->clearDirIndexes();
        pContext->addDirIndexes(list.c_str());
    }
    else if (strcasecmp(pName, "AddType") == 0)
    {
        addSuffixes(pContext, isPhpType(pArg) ? HTA_PHP_TYPE : pArg, args);
    }
    else if (strcasecmp(pName, "AddHandler") == 0)
    {
        if (isPhpType(pArg))
            addSuffixes(pContext, HTA_PHP_TYPE, args);
    }
    else if (strcasecmp(pName, "SetHandler") == 0)
    {
        if (isPhpType(pArg))
            forceType(b, pContext, HTA_PHP_TYPE);
        else if (strcasecmp(pArg, "default-handler") == 0)
            forceType(b, pContext, "text/plain");
    }
    else if (strcasecmp(pName, "ForceType") == 0)
        forceType(b, pContext, isPhpType(pArg) ? HTA_PHP_TYPE : pArg);
    else if (strcasecmp(pName, "AddDefaultCharset") == 0)
    {
        if (isOn(pArg))
            pContext->setDefaultCharsetOn();
        else if (strcasecmp(pArg, "off") != 0)
            pContext->setDefaultCharset(pArg);
    }
    else if (strcasecmp(pName, "Options") == 0)
        applyOptions(pContext, dir);
    else if (strcasecmp(pName, "php_value") == 0
             || strcasecmp(pName, "php_flag") == 0)
    {
        if (args.size() < 2)
            warn(b, dir, "invalid PHP setting");
        else
        {
            std::string value = args[0] + " " + args[1];
            char achError[128];
            if (!b.m_pPHP)
                b.m_pPHP = new PHPConfig();
            b.m_pPHP->parse(tolower(pName[4]) == 'v' ? PHP_VALUE : PHP_FLAG,
                            value.c_str(), achError, sizeof(achError));
        }
    }
    else if (strcasecmp(pName, "php_admin_value") == 0
             || strcasecmp(pName, "php_admin_flag") == 0)
        //Apache refuses these here: they would let a tenant lift admin limits.
        warn(b, dir, "administrator setting not allowed in .htaccess, ignored");
    else if (strcasecmp(pName, "SetEnv") == 0)
    {
        if (args.empty() || reservedEnv(args[0]))
            warn(b, dir, "invalid environment setting");
        else
        {
            HtaEnvOp op;
            op.m_sCond = b.m_sCond;
            HtaEnvSet set;
            set.m_sName = args[0];
            set.m_sValue = args.size() > 1 ? args[1] : "";
            set.m_bUnset = false;
            op.m_sets.push_back(set);
            ownRules(pContext)->m_envOps.push_back(op);
        }
    }
    else if (strcasecmp(pName, "UnsetEnv") == 0)
    {
        HtaEnvOp op;
        op.m_sCond = b.m_sCond;
        op.m_type = HtaEnvOp::UNSET;
        for (size_t i = 0; i < args.size(); ++i)
        {
            HtaEnvSet set;
            set.m_sName = args[i];
            set.m_bUnset = true;
            op.m_sets.push_back(set);
        }
        ownRules(pContext)->m_envOps.push_back(op);
    }
    else if (strcasecmp(pName, "SetEnvIf") == 0
             || strcasecmp(pName, "SetEnvIfNoCase") == 0)
    {
        if (!addEnvMatch(b, dir, ownRules(pContext), pArg, 1,
                         strcasecmp(pName, "SetEnvIfNoCase") == 0))
            warn(b, dir, "invalid environment rule");
    }
    else if (strcasecmp(pName, "BrowserMatch") == 0
             || strcasecmp(pName, "BrowserMatchNoCase") == 0)
    {
        if (!addEnvMatch(b, dir, ownRules(pContext), "User-Agent", 0,
                         strcasecmp(pName, "BrowserMatchNoCase") == 0))
            warn(b, dir, "invalid environment rule");
    }
    else if (strcasecmp(pName, "Redirect") == 0
             || strcasecmp(pName, "RedirectPermanent") == 0
             || strcasecmp(pName, "RedirectTemp") == 0
             || strcasecmp(pName, "RedirectMatch") == 0)
    {
        int forced = 0;
        if (strcasecmp(pName, "RedirectPermanent") == 0)
            forced = SC_301;
        else if (strcasecmp(pName, "RedirectTemp") == 0)
            forced = SC_302;
        if (!addRedirect(b, dir, ownRules(pContext), forced,
                         strcasecmp(pName, "RedirectMatch") == 0))
            warn(b, dir, "invalid redirect");
    }
    else if (!isIgnored(pName))
    {
        warn(b, dir, "unsupported directive ignored");
        if (b.m_pVHost->getHtaMode() == HttpVHost::HTA_STRICT)
            ownRules(pContext)->m_bBroken = true;
    }
}


static void applyList(HtaBuild &b, HttpContext *pContext,
                      const std::vector<HtaDirective> &list, uint64_t methods)
{
    for (size_t i = 0; i < list.size(); ++i)
        applyOne(b, pContext, list[i], methods);
}


static HtaRules *ownRules(HttpContext *pContext)
{
    if (!(pContext->getConfigBits2() & BIT2_HTA_OWN_RULES))
    {
        pContext->setHtaRules(new HtaRules());
        pContext->setConfigBit2(BIT2_HTA_OWN_RULES, 1);
    }
    return pContext->getHtaRules();
}


static void finishContext(const HtaBuild &b, HttpContext *pContext)
{
    if (pContext->getConfigBits2() & BIT2_HTA_OWN_RULES)
        finishAuth(b, pContext->getHtaRules());
}


// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

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
        //Removed after stat(): same as not having one.
        if (errno == ENOENT)
        {
            stamp.m_iPresent = 0;
            return 0;
        }
        //Whatever the file protects must not become public.
        LS_WARN("[%s] Cannot read %s, denying access.", pVHost->getName(), achPath);
        ownRules(pContext)->m_bDenyAll = true;
        return LS_FAIL;
    }

    std::vector<HtaDirective> directives;
    int errLine = parse(content.c_str(), directives);
    if (errLine)
    {
        LS_WARN("[%s] %s:%d: syntax error.", pVHost->getName(), achPath, errLine);
        ownRules(pContext)->m_bBroken = true;
        return LS_FAIL;
    }

    HtaBuild build;
    build.m_pVHost = pVHost;
    build.m_pFile = achPath;
    build.m_pPHP = NULL;
    applyList(build, pContext, directives, HTA_ALL_METHODS);
    finishContext(build, pContext);
    if (build.m_pPHP)
        pContext->setPHPConfig(build.m_pPHP);

    if (!build.m_sRewrite.empty())
    {
        RewriteRuleList *pRules = new RewriteRuleList();
        RewriteRule::setLogger(NULL, TmpLogId::getLogId());
        char *pText = &build.m_sRewrite[0];
        if (RewriteEngine::parseRules(pText, pRules, pVHost->getRewriteMaps(),
                                      pContext) == 0 && pRules->begin())
            pContext->setRewriteRules(pRules);
        else
            delete pRules;
    }
    return 0;
}


// ---------------------------------------------------------------------------
// Request time
// ---------------------------------------------------------------------------

static const HtaRules *activeRules(HttpSession *pSession)
{
    HttpReq *pReq = pSession->getReq();
    const HttpContext *pFiles = pReq->getFMContext();
    if (pFiles && pFiles->getHtaRules())
        return pFiles->getHtaRules();
    const HttpContext *pContext = pReq->getContext();
    return pContext ? pContext->getHtaRules() : NULL;
}


static bool hasEnv(HttpSession *pSession, const std::string &name);
static bool netMatches(const HtaAddr &addr, const unsigned char *pPeer);


/** Well known headers are kept by index, and only the others by name. */
static const char *reqHeader(const HttpReq *pReq, const char *pName, int nameLen,
                             int &valLen)
{
    size_t index = HttpHeader::getIndex(pName, nameLen);
    if (index < HttpHeader::H_HEADER_END)
    {
        if (!pReq->isHeaderSet(index))
            return NULL;
        valLen = pReq->getHeaderLen(index);
        return pReq->getHeader(index);
    }
    return pReq->getHeader(pName, nameLen, valLen);
}


static std::string exprVar(HttpSession *pSession, const std::string &name)
{
    HttpReq *pReq = pSession->getReq();
    int len = 0;
    if (name.compare(0, 5, "HTTP:") == 0)
    {
        const char *p = reqHeader(pReq, name.c_str() + 5, name.size() - 5, len);
        return p ? std::string(p, len) : std::string();
    }
    if (name.compare(0, 5, "HTTP_") == 0 && name != "HTTP_VERSION")
    {
        std::string header = name.substr(5);
        for (size_t i = 0; i < header.size(); ++i)
            if (header[i] == '_')
                header[i] = '-';
        const char *p = reqHeader(pReq, header.c_str(), header.size(), len);
        return p ? std::string(p, len) : std::string();
    }
    int type = RequestVars::parseBuiltIn(name.c_str(), name.size());
    if (type >= 0)
    {
        char achBuf[8192];
        char *pValue = achBuf;
        len = RequestVars::getReqVar(pSession, type, pValue, sizeof(achBuf));
        return len > 0 ? std::string(pValue, len) : std::string();
    }
    const char *p = pReq->getEnv(name.c_str(), name.size(), len);
    return p ? std::string(p, len) : std::string();
}


static std::string exprValue(HttpSession *pSession, const HtaExpr *pExpr)
{
    const std::string &text = pExpr->m_sText;
    if (pExpr->m_type == HtaExpr::REQ_HEADER)
    {
        int len = 0;
        const char *p = reqHeader(pSession->getReq(), text.c_str(), text.size(), len);
        return p ? std::string(p, len) : std::string();
    }
    std::string out;
    size_t pos = 0;
    while (pos < text.size())
    {
        size_t begin = text.find("%{", pos);
        size_t end = begin == std::string::npos ? begin : text.find('}', begin);
        if (end == std::string::npos)
        {
            out.append(text, pos, std::string::npos);
            break;
        }
        out.append(text, pos, begin - pos);
        out += exprVar(pSession, text.substr(begin + 2, end - begin - 2));
        pos = end + 1;
    }
    return out;
}


static bool exprTrue(HttpSession *pSession, const HtaExpr *pExpr)
{
    switch (pExpr->m_type)
    {
    case HtaExpr::TRUE_:
        return true;
    case HtaExpr::FALSE_:
        return false;
    case HtaExpr::OR:
        return exprTrue(pSession, pExpr->m_pLeft)
               || exprTrue(pSession, pExpr->m_pRight);
    case HtaExpr::AND:
        return exprTrue(pSession, pExpr->m_pLeft)
               && exprTrue(pSession, pExpr->m_pRight);
    case HtaExpr::NOT:
        return !exprTrue(pSession, pExpr->m_pLeft);
    default:
        break;
    }

    std::string left = exprValue(pSession, pExpr->m_pLeft);
    switch (pExpr->m_type)
    {
    case HtaExpr::NONEMPTY:
        return !left.empty();
    case HtaExpr::EMPTY:
        return left.empty();
    case HtaExpr::MATCH:
    case HtaExpr::NOMATCH:
    {
        int ovector[6];
        bool found = pExpr->m_pRegex->exec(left.c_str(), left.size(), 0, 0,
                                           ovector, 6) >= 0;
        return found == (pExpr->m_type == HtaExpr::MATCH);
    }
    case HtaExpr::IPMATCH:
    {
        HtaAddr peer;
        return parseAddr(left, peer) && netMatches(pExpr->m_addr, peer.m_addr);
    }
    default:
        break;
    }

    std::string right = exprValue(pSession, pExpr->m_pRight);
    long a = strtol(left.c_str(), NULL, 10);
    long b = strtol(right.c_str(), NULL, 10);
    int cmp = left.compare(right);
    switch (pExpr->m_type)
    {
    case HtaExpr::EQ:       return cmp == 0;
    case HtaExpr::NE:       return cmp != 0;
    case HtaExpr::LT:       return cmp < 0;
    case HtaExpr::LE:       return cmp <= 0;
    case HtaExpr::GT:       return cmp > 0;
    case HtaExpr::GE:       return cmp >= 0;
    case HtaExpr::NUM_EQ:   return a == b;
    case HtaExpr::NUM_NE:   return a != b;
    case HtaExpr::NUM_LT:   return a < b;
    case HtaExpr::NUM_LE:   return a <= b;
    case HtaExpr::NUM_GT:   return a > b;
    case HtaExpr::NUM_GE:   return a >= b;
    default:                return false;
    }
}


static void applyEnvChain(HttpSession *pSession, const HtaRules *pRules)
{
    if (pRules->m_pParent)
        applyEnvChain(pSession, pRules->m_pParent);
    HttpReq *pReq = pSession->getReq();
    for (size_t i = 0; i < pRules->m_envOps.size(); ++i)
    {
        const HtaEnvOp &op = pRules->m_envOps[i];
        if (!op.m_sCond.empty() && !hasEnv(pSession, op.m_sCond))
            continue;
        if (op.m_type == HtaEnvOp::COND)
        {
            bool active = true;
            for (size_t n = 0; active && n < op.m_previous.size(); ++n)
                active = !hasEnv(pSession, op.m_previous[n]);
            if (active && (!op.m_pExpr || exprTrue(pSession, op.m_pExpr)))
                pReq->addEnv(op.m_sAttr.c_str(), op.m_sAttr.size(), "1", 1);
            continue;
        }
        if (op.m_type == HtaEnvOp::MATCH)
        {
            const char *pAttr = op.m_sAttr.c_str();
            const char *pValue = NULL;
            int len = 0;
            if (strcasecmp(pAttr, "Remote_Addr") == 0
                || strcasecmp(pAttr, "Remote_Host") == 0)
            {
                pValue = pSession->getPeerAddrString();
                len = pSession->getPeerAddrStrLen();
            }
            else if (strcasecmp(pAttr, "Request_URI") == 0)
            {
                pValue = pReq->getURI();
                len = pReq->getURILen();
            }
            else if (strcasecmp(pAttr, "Request_Method") == 0)
            {
                pValue = HttpMethod::get((http_method_t)pReq->getMethod());
                len = strlen(pValue);
            }
            else
            {
                pValue = reqHeader(pReq, pAttr, op.m_sAttr.size(), len);
                if (!pValue)
                    pValue = pReq->getEnv(pAttr, op.m_sAttr.size(), len);
            }
            int ovector[6];
            if (!pValue
                || op.m_pRegex->exec(pValue, len, 0, 0, ovector, 6) < 0)
                continue;
        }
        for (size_t n = 0; n < op.m_sets.size(); ++n)
        {
            const HtaEnvSet &set = op.m_sets[n];
            if (set.m_bUnset)
                pReq->unsetEnv(set.m_sName.c_str(), set.m_sName.size());
            else
                pReq->addEnv(set.m_sName.c_str(), set.m_sName.size(),
                             set.m_sValue.c_str(), set.m_sValue.size());
        }
    }
}


void Htaccess::applyEnv(HttpSession *pSession)
{
    const HttpContext *pContext = pSession->getReq()->getContext();
    if (pContext && pContext->getHtaRules())
        applyEnvChain(pSession, pContext->getHtaRules());
}


static bool peerBytes(HttpSession *pSession, unsigned char *pBytes)
{
    const struct sockaddr *pAddr = pSession->getPeerAddr();
    if (!pAddr)
        return false;
    if (pAddr->sa_family == AF_INET)
    {
        memset(pBytes, 0, 10);
        pBytes[10] = pBytes[11] = 0xff;
        memcpy(pBytes + 12, &((const struct sockaddr_in *)pAddr)->sin_addr, 4);
        return true;
    }
    if (pAddr->sa_family == AF_INET6)
    {
        memcpy(pBytes, &((const struct sockaddr_in6 *)pAddr)->sin6_addr, 16);
        return true;
    }
    return false;
}


static bool netMatches(const HtaAddr &addr, const unsigned char *pPeer)
{
    int bits = addr.m_iBits;
    int bytes = bits / 8;
    if (memcmp(addr.m_addr, pPeer, bytes) != 0)
        return false;
    if (bits % 8 == 0)
        return true;
    unsigned char mask = (unsigned char)(0xff << (8 - bits % 8));
    return (addr.m_addr[bytes] & mask) == (pPeer[bytes] & mask);
}


static bool hasEnv(HttpSession *pSession, const std::string &name)
{
    int len = 0;
    const char *pValue = pSession->getReq()->getEnv(name.c_str(), name.size(), len);
    return pValue && len > 0;
}


/** Host names need a reverse lookup, which is not available here: no match. */
static bool addrMatches(HttpSession *pSession, const std::vector<HtaAddr> &list,
                        uint64_t methodBit)
{
    unsigned char peer[16];
    bool havePeer = peerBytes(pSession, peer);
    for (size_t i = 0; i < list.size(); ++i)
    {
        const HtaAddr &addr = list[i];
        if (!(addr.m_methods & methodBit))
            continue;
        switch (addr.m_type)
        {
        case HtaAddr::ALL:
            return true;
        case HtaAddr::NET:
            if (havePeer && netMatches(addr, peer))
                return true;
            break;
        case HtaAddr::ENV:
            if (hasEnv(pSession, addr.m_sEnv) != addr.m_bNegate)
                return true;
            break;
        case HtaAddr::HOST:
            break;
        }
    }
    return false;
}


/** Each list keeps the value of the nearest directory that defines it. */
static bool hostAllowed(HttpSession *pSession, const HtaRules *pRules,
                        uint64_t methodBit, bool &defined)
{
    int order = ORDER_UNSET;
    const std::vector<HtaAddr> *pAllows = NULL;
    const std::vector<HtaAddr> *pDenies = NULL;
    for (const HtaRules *p = pRules; p; p = p->m_pParent)
    {
        for (size_t i = 0; order == ORDER_UNSET && i < p->m_orders.size(); ++i)
            if (p->m_orders[i].m_methods & methodBit)
                order = p->m_orders[i].m_iOrder;
        if (!pAllows && !p->m_allows.empty())
            pAllows = &p->m_allows;
        if (!pDenies && !p->m_denies.empty())
            pDenies = &p->m_denies;
    }
    defined = order != ORDER_UNSET || pAllows || pDenies;
    if (!defined)
        return true;
    bool allow = pAllows && addrMatches(pSession, *pAllows, methodBit);
    bool deny = pDenies && addrMatches(pSession, *pDenies, methodBit);
    switch (order)
    {
    case ORDER_ALLOW_DENY:
        return allow && !deny;
    case ORDER_MUTUAL:
        return allow && !deny;
    default:
        return allow || !deny;
    }
}


static int evalRequire(HttpSession *pSession, const HtaRequire *pReq,
                       const HTAuth *pAuth, const char *pUser, uint64_t methodBit);


static int evalChildren(HttpSession *pSession, const HtaRequire *pReq,
                        const HTAuth *pAuth, const char *pUser,
                        uint64_t methodBit)
{
    bool granted = false, denied = false, needUser = false;
    for (size_t i = 0; i < pReq->m_children.size(); ++i)
    {
        int result = evalRequire(pSession, pReq->m_children[i], pAuth, pUser,
                                 methodBit);
        granted |= (result == AZ_GRANTED);
        denied |= (result == AZ_DENIED);
        needUser |= (result == AZ_NEED_USER);
    }
    switch (pReq->m_type)
    {
    case HtaRequire::ANY:
        if (granted)
            return AZ_GRANTED;
        if (needUser)
            return AZ_NEED_USER;
        return denied ? AZ_DENIED : AZ_NEUTRAL;
    case HtaRequire::ALL_OF:
        if (denied)
            return AZ_DENIED;
        if (needUser)
            return AZ_NEED_USER;
        return granted ? AZ_GRANTED : AZ_NEUTRAL;
    default:    //NONE
        if (granted)
            return AZ_DENIED;
        return needUser ? AZ_NEED_USER : AZ_NEUTRAL;
    }
}


static int evalRequire(HttpSession *pSession, const HtaRequire *pReq,
                       const HTAuth *pAuth, const char *pUser, uint64_t methodBit)
{
    int result = AZ_DENIED;
    switch (pReq->m_type)
    {
    case HtaRequire::ANY:
    case HtaRequire::ALL_OF:
    case HtaRequire::NONE:
        return evalChildren(pSession, pReq, pAuth, pUser, methodBit);
    default:
        break;
    }
    if (!(pReq->m_methods & methodBit)
        || (!pReq->m_sCond.empty() && !hasEnv(pSession, pReq->m_sCond)))
        return AZ_NEUTRAL;

    switch (pReq->m_type)
    {
    case HtaRequire::ALL_GRANTED:
        result = AZ_GRANTED;
        break;
    case HtaRequire::IP:
        if (addrMatches(pSession, pReq->m_addrs, methodBit))
            result = AZ_GRANTED;
        break;
    case HtaRequire::ENV:
        for (size_t i = 0; i < pReq->m_args.size(); ++i)
            if (hasEnv(pSession, pReq->m_args[i]))
                result = AZ_GRANTED;
        break;
    case HtaRequire::METHOD:
        for (size_t i = 0; i < pReq->m_args.size(); ++i)
        {
            int bit = methodIndex(pReq->m_args[i].c_str());
            if (((uint64_t)1 << bit) == methodBit)
                result = AZ_GRANTED;
        }
        break;
    case HtaRequire::VALID_USER:
        if (!pUser)
            return AZ_NEED_USER;
        result = AZ_GRANTED;
        break;
    case HtaRequire::USER:
        if (!pUser)
            return AZ_NEED_USER;
        for (size_t i = 0; i < pReq->m_args.size(); ++i)
            if (pReq->m_args[i] == pUser)
                result = AZ_GRANTED;
        break;
    case HtaRequire::GROUP:
        if (!pUser)
            return AZ_NEED_USER;
        if (pAuth && pReq->m_pGroups)
        {
            int ready = 0;
            UserDir *pDir = (UserDir *)pAuth->getUserDir();
            if (pDir->getRequiredUser(pSession, pUser, strlen(pUser),
                                      pReq->m_pGroups, &ready))
                result = AZ_GRANTED;
        }
        break;
    default:    //ALL_DENIED, HOST
        break;
    }
    if (pReq->m_bNegate)
        return result == AZ_GRANTED ? AZ_DENIED : AZ_NEUTRAL;
    return result;
}


int Htaccess::checkAccess(HttpSession *pSession)
{
    const HtaRules *pRules = activeRules(pSession);
    //The error page of a denied request lives under the same rules.
    if (!pRules || pSession->getReq()->isErrorPage())
        return 0;

    const HtaRules *pAuthRules = NULL;
    const HtaRequire *pRequire = NULL;
    int satisfy = SATISFY_UNSET;
    for (const HtaRules *p = pRules; p; p = p->m_pParent)
    {
        if (p->m_bBroken)
            return SC_500;
        if (p->m_bDenyAll)
            return SC_403;
        if (!pRequire)
            pRequire = p->m_pRequire;
        if (!pAuthRules && p->m_pAuth)
            pAuthRules = p;
        if (satisfy == SATISFY_UNSET)
            satisfy = p->m_iSatisfy;
    }

    HttpReq *pReq = pSession->getReq();
    uint64_t methodBit = (uint64_t)1 << (pReq->getMethod() & 63);
    bool compat = false;
    bool allowed = hostAllowed(pSession, pRules, methodBit, compat);
    if (compat && allowed && satisfy == SATISFY_ANY)
        return 0;
    if (!allowed && satisfy != SATISFY_ANY)
        return SC_403;

    const HTAuth *pAuth = pAuthRules ? pAuthRules->m_pAuth : NULL;
    int result = pRequire ? evalRequire(pSession, pRequire, pAuth, NULL, methodBit)
                 : AZ_NEUTRAL;
    if (result == AZ_NEED_USER)
    {
        if (!pAuth)
        {
            LS_WARN(pSession->getLogSession(),
                    "[HTA] Require needs a user but no AuthType/AuthUserFile is set.");
            return SC_500;
        }
        pReq->setHtAuth(pAuth, pAuthRules->m_pValidUser);
        int ret = pSession->checkAuthentication(pAuth, pAuthRules->m_pValidUser, 0);
        if (ret > 0)
            return ret;
        if (ret < 0)
            return SC_401;
        result = evalRequire(pSession, pRequire, pAuth, pReq->getAuthUser(),
                             methodBit);
        if (result != AZ_GRANTED && result != AZ_NEUTRAL)
            return SC_401;
        //Otherwise the response would carry a needless challenge.
        pReq->setHtAuth(NULL, NULL);
        return 0;
    }
    if (result == AZ_DENIED)
        return SC_403;
    if (!allowed && result != AZ_GRANTED)
        return SC_403;
    return 0;
}


/** A nearer directory wins, as Apache puts child redirects first. */
static const HtaRedirect *findRedirect(HttpSession *pSession,
                                       const HtaRules *pRules, const char *pURI,
                                       int len, int *pOvector, int &matches)
{
    for (const HtaRules *p = pRules; p; p = p->m_pParent)
    {
        for (size_t i = 0; i < p->m_redirects.size(); ++i)
        {
            const HtaRedirect &r = p->m_redirects[i];
            if (!r.m_sCond.empty() && !hasEnv(pSession, r.m_sCond))
                continue;
            if (r.m_pRegex)
            {
                matches = r.m_pRegex->exec(pURI, len, 0, 0, pOvector, 30);
                if (matches >= 0)
                    return &r;
            }
            else
            {
                size_t n = r.m_sPath.size();
                if (strncmp(pURI, r.m_sPath.c_str(), n) == 0
                    && (pURI[n] == 0 || pURI[n] == '/' || (n && pURI[n - 1] == '/')))
                {
                    matches = -1;
                    return &r;
                }
            }
        }
    }
    return NULL;
}


int Htaccess::checkRedirect(HttpSession *pSession)
{
    HttpReq *pReq = pSession->getReq();
    const HttpContext *pContext = pReq->getContext();
    if (!pContext || !pContext->getHtaRules())
        return 0;

    const char *pURI = pReq->getURI();
    int len = pReq->getURILen();
    int ovector[30];
    int matches = -1;
    const HtaRedirect *pRedirect = findRedirect(pSession, pContext->getHtaRules(),
                                   pURI, len, ovector, matches);
    if (!pRedirect)
        return 0;
    if (pRedirect->m_iStatus >= SC_400)
        return pRedirect->m_iStatus;

    std::string target;
    if (pRedirect->m_pRegex)
    {
        const char *p = pRedirect->m_sTarget.c_str();
        for (; *p; ++p)
        {
            if (*p == '$' && isdigit((unsigned char)p[1]))
            {
                int group = *++p - '0';
                if (group < matches && ovector[group * 2] >= 0)
                    target.append(pURI + ovector[group * 2],
                                  ovector[group * 2 + 1] - ovector[group * 2]);
            }
            else
                target += *p;
        }
    }
    else
    {
        target = pRedirect->m_sTarget;
        target.append(pURI + pRedirect->m_sPath.size());
    }
    if (pReq->getQueryStringLen() > 0 && target.find('?') == std::string::npos)
    {
        target += '?';
        target.append(pReq->getQueryString(), pReq->getQueryStringLen());
    }
    pReq->setLocation(target.c_str(), target.size());
    return pRedirect->m_iStatus;
}
