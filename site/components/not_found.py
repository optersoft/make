"""The 404 body: the chrome's, with this site's words."""

import optersoft_brand as brand

TITLE = "Not found — mkrun"
DESCRIPTION = "No such page on make.optersoft.com."


def body():
    return brand.not_found(
        "No such page",
        "There is one page here, and this is not it.",
        home={"href": "/", "label": "Back to mkrun"},
        other={"href": "https://academy.optersoft.com/project/make", "label": "The tutorial"},
    )
