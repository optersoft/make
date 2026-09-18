"""What the landing page is made of: a band, a card, and the two kinds of code block.

The terminal and the listing are spans with one-letter classes — `.p` a prompt, `.c` a
comment, `.k` a keyword — styled in `tailwind.css`. Deliberately not a highlighter: these
are six lines of shell and eight of Python, and a tokeniser here would be a dependency, a
runtime, or both.
"""

from frontage import h

#: A pill link in the hero.
PILL = (
    "inline-flex items-center rounded-full border border-slate-200 bg-white px-4 py-1.5 text-sm "
    "font-medium text-slate-700 transition-colors hover:border-blue-300 hover:text-blue-700 "
    "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-blue-500/50 "
    "dark:hover:text-blue-300"
)

#: A link inside prose.
LINK = "font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"


def section(ident, heading, *children, alt=False):
    """One band of the page: an anchor, an h2, then its content.

    Alternating surfaces, the way optersoft.com's bands do.
    """
    surface = (
        "scroll-mt-20 bg-slate-50 dark:bg-slate-900/40"
        if alt
        else "scroll-mt-20 border-t border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950"
    )
    return h.section(
        h.div(
            h.h2(
                heading,
                cls=(
                    "text-3xl sm:text-4xl font-extrabold tracking-tight text-balance "
                    "text-slate-900 dark:text-white"
                ),
            ),
            h.div(
                *children,
                cls="prose-mk mt-5 text-slate-600 leading-relaxed dark:text-slate-300",
            ),
            cls="container mx-auto px-5 sm:px-6 py-14 sm:py-16 max-w-6xl",
        ),
        id=ident,
        cls=surface,
    )


def card(title, *children):
    return h.div(
        h.h3(title, cls="text-base font-semibold text-slate-900 dark:text-white"),
        h.p(
            *children,
            cls="prose-mk mt-1.5 text-sm text-slate-600 leading-relaxed dark:text-slate-300",
        ),
        cls="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900",
    )


def cards(*children, columns="sm:grid-cols-2 lg:grid-cols-3"):
    return h.div(*children, cls=f"mt-8 grid gap-4 {columns}")


def code(text, cls="code", **rest):
    """A listing. `text` is a list of strings and `mark()` spans."""
    return h.pre(*text, cls=cls, **rest)


def mark(kind, text):
    """One highlighted run: `p` a prompt, `c` a comment, `k` a keyword."""
    return h.span(text, cls=kind)


def prompt(text):
    return mark("p", text)


def comment(text):
    return mark("c", text)


def kw(text):
    return mark("k", text)


def a(href, label, cls=LINK):
    return h.a(label, href=href, cls=cls)
