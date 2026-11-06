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
#ifdef RUN_TEST

#include <http/htaccess.h>

#include "unittest-cpp/UnitTest++.h"

typedef std::vector<HtaDirective> Directives;


TEST(htaccess_parse_directive_args)
{
    Directives list;
    CHECK_EQUAL(0, Htaccess::parse("Header set X-A \"a b\"\n", list));
    CHECK_EQUAL(1u, list.size());
    CHECK_EQUAL("Header", list[0].m_sName);
    CHECK_EQUAL(3u, list[0].m_args.size());
    CHECK_EQUAL("a b", list[0].m_args[2]);
    CHECK_EQUAL("Header set X-A \"a b\"", list[0].m_sLine);
}


TEST(htaccess_parse_skips_comments_and_blank_lines)
{
    Directives list;
    CHECK_EQUAL(0, Htaccess::parse("# one\n\n   # two\nOptions -Indexes\r\n", list));
    CHECK_EQUAL(1u, list.size());
    CHECK_EQUAL("-Indexes", list[0].m_args[0]);
    CHECK_EQUAL(4, list[0].m_iLine);
}


TEST(htaccess_parse_line_continuation)
{
    Directives list;
    CHECK_EQUAL(0, Htaccess::parse("Header set \\\n  X-A \\\n  1\n", list));
    CHECK_EQUAL(1u, list.size());
    CHECK_EQUAL(3u, list[0].m_args.size());
    CHECK_EQUAL("1", list[0].m_args[2]);
}


TEST(htaccess_parse_nested_blocks)
{
    Directives list;
    CHECK_EQUAL(0, Htaccess::parse(
        "<IfModule mod_headers.c>\n"
        "  <FilesMatch \"\\.css$\">\n"
        "    Header set X-A 1\n"
        "  </FilesMatch>\n"
        "</IfModule>\n", list));
    CHECK_EQUAL(1u, list.size());
    CHECK(list[0].m_bBlock);
    CHECK_EQUAL("mod_headers.c", list[0].m_args[0]);
    CHECK_EQUAL(1u, list[0].m_children.size());
    CHECK_EQUAL("\\.css$", list[0].m_children[0].m_args[0]);
    CHECK_EQUAL(1u, list[0].m_children[0].m_children.size());
}


TEST(htaccess_parse_block_names_ignore_case)
{
    Directives list;
    CHECK_EQUAL(0, Htaccess::parse("<ifmodule x>\n</IFMODULE>\n", list));
    CHECK_EQUAL(1u, list.size());
}


TEST(htaccess_parse_reports_the_failing_line)
{
    Directives list;
    CHECK_EQUAL(2, Htaccess::parse("Options None\n</IfModule>\n", list));
    list.clear();
    CHECK_EQUAL(1, Htaccess::parse("<IfModule x>\nOptions None\n", list) ? 1 : 0);
    list.clear();
    CHECK_EQUAL(2, Htaccess::parse("<Files a>\n</FilesMatch>\n", list));
}


TEST(htaccess_parse_limits_nesting)
{
    std::string text;
    for (int i = 0; i < 100; ++i)
        text += "<IfModule x>\n";
    Directives list;
    CHECK(Htaccess::parse(text.c_str(), list) != 0);
}


TEST(htaccess_parse_empty_file)
{
    Directives list;
    CHECK_EQUAL(0, Htaccess::parse("", list));
    CHECK_EQUAL(0u, list.size());
}

#endif
