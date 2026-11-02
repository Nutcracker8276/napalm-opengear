"""netutils config parser for Opengear's `config --show-config` output.

netutils has no Opengear parser, so Golden Config fails compliance on the
platform. Importing this module registers one under the Nautobot platform slug.
Every Nautobot process evaluates compliance, so import it from nautobot_config.py:

    import napalm_opengear.netutils_parser  # noqa: F401

netutils comes with Golden Config, so it is not a requirement of this package.
"""

from netutils.config.compliance import parser_map
from netutils.config.parser import BaseSpaceConfigParser


class OpengearConfigParser(BaseSpaceConfigParser):
    """Indentation-based parser that keeps a multi-line banner with its entity."""

    comment_chars = []
    banner_start = ['banner """']
    banner_end = '"""'

    def is_banner_start(self, line):
        """Only a banner whose closing quotes are on a later line needs collecting."""
        return line.lstrip().startswith(self.banner_start[0]) and line.count('"""') == 1

    def is_banner_end(self, line):
        """netutils stores the end as ^C, then parses that text again to compare it."""
        return self.banner_end in line or '^C' in line


parser_map['opengear'] = OpengearConfigParser
