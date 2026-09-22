# Wave 1 H3C fixtures

The six `display_*_s10500x.json` / `display_*_s12500.json` SSH fixtures are
anonymized real-output shapes captured from S10510X and S12508G-AF Comware 7
devices. They preserve parser-significant model/version fields, table headers,
MemberID/Slot/Role/Priority columns, `*`/`+` markers, repeated MPU-slot rows and
IRF-Port layout. MAC addresses, descriptions, hostnames, management addresses
and authentication data are removed or replaced.

The SNMP JSON fixtures remain synthetic test inputs unless their `_comment`
explicitly says otherwise. Real-device validation confirmed the production
SNMP collection path separately; this compatibility fix does not rewrite
those already-verified collectors or represent raw site data in Git.

These fixtures prove parser behavior only. W01-T007 remains pending until an
image containing the corrected parser is taken back to the field and the
normalized S12508G-AF model and both IRF member-role results are revalidated.
