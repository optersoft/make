"""make.optersoft.com: what the whole site knows about itself.

Built by `frontage site`. The chrome is `optersoft_brand`, a sibling checkout consumed by
path like every other dependency in this fleet.
"""

import optersoft_brand

BASE = "https://make.optersoft.com"

#: The chrome's stylesheets, fonts and mark, at `/brand/` — where `brand.css` looks for them.
#: This site's own favicon stays in `public/`, at a literal path.
STATIC = [(optersoft_brand.static, "brand")]
