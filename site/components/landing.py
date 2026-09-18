"""The landing page.

The case for using the tool: what it is, who it is for, why it is worth the runtime it
costs, and where it is going. Not documentation — the authoring API is in the README, the
reasoning in `docs/design.md`, the tutorial on academy — and not a release feed.
"""

import optersoft_brand as brand
from frontage import h

from components.parts import PILL, a, card, cards, code, comment, kw, prompt, section

TITLE = "mkrun — a command runner whose tasks are Python"
DESCRIPTION = (
    "mkrun is a command runner whose tasks are ordinary Python functions: the command line "
    "comes from the signature, and task packages ship on PyPI instead of being copied "
    "between repositories."
)

LINKS = [
    ("https://pypi.org/project/mkrun/", "PyPI · mkrun"),
    ("https://github.com/optersoft/make", "GitHub · optersoft/make"),
    ("https://academy.optersoft.com/project/make", "Tutorial · academy"),
    ("#worth", "Why it is worth it"),
]


def hero():
    return h.section(
        brand.backdrop(glow=True),
        h.div(
            h.h1(
                "mk",
                h.span("_", cls="text-blue-600 dark:text-blue-400"),
                cls="font-mono text-5xl sm:text-6xl font-extrabold tracking-tight text-slate-900 dark:text-white",
            ),
            h.p(
                "A command runner whose tasks are Python. The command line comes from the function "
                "signature, so there is no second schema to keep in sync.",
                cls="mt-4 max-w-2xl text-lg sm:text-xl text-slate-600 leading-relaxed dark:text-slate-300",
            ),
            h.ul(
                *[h.li(h.a(label, href=href, cls=PILL)) for href, label in LINKS],
                cls="mt-7 flex flex-wrap gap-2",
            ),
            code(
                [
                    comment("# install the runner -- one command, `mk`"),
                    "\n",
                    prompt("$"),
                    " uv tool install mkrun\n\n",
                    comment("# then, in any repository with a Makefile.py"),
                    "\n",
                    prompt("$"),
                    " mk app.test --fast\n",
                    prompt("$"),
                    " cargo test --lib",
                ],
                cls="term mt-8 max-w-3xl",
            ),
            cls="relative container mx-auto px-5 sm:px-6 py-16 sm:py-20 lg:py-24 max-w-6xl",
        ),
        cls="relative overflow-hidden bg-white border-b border-slate-200 dark:bg-slate-950 dark:border-slate-800",
    )


def what():
    return section(
        "what",
        "What it is",
        h.p(
            "A task file is Python. A task is a function with a docstring; its parameters become the "
            "command line, its docstring becomes the help, and its module becomes a namespace. Nothing "
            "is interpolated into a string, so a value with a space, a quote or a ",
            h.code("$"),
            " stays data.",
            cls="max-w-3xl text-lg",
        ),
        code(
            [
                comment("# Makefile.py"),
                "\n",
                kw("from"),
                " make ",
                kw("import"),
                " task, sh\n\n",
                kw("@task"),
                "(group=",
                kw('"app"'),
                ", requires=[",
                kw('"cargo"'),
                "])\n",
                kw("def"),
                " test(*, fast: bool = ",
                kw("False"),
                ") -> ",
                kw("None"),
                ":\n    ",
                comment('"""Run the test suite."""'),
                "\n    sh(",
                kw('"cargo"'),
                ", ",
                kw('"test"'),
                ", *([",
                kw('"--lib"'),
                "] ",
                kw("if"),
                " fast ",
                kw("else"),
                " []))",
            ],
            cls="code mt-6 max-w-3xl",
        ),
        h.p(
            h.strong("Three names, deliberately different.", cls="text-slate-900 dark:text-white"),
            " The distribution is ",
            h.code("mkrun"),
            ", the import is ",
            h.code("make"),
            ", the command is ",
            h.code("mk"),
            ". Nothing installs a ",
            h.code("make"),
            " command — that would shadow GNU make on the ",
            h.code("PATH"),
            " of every Unix machine.",
            cls="mt-6 max-w-3xl",
        ),
        cards(
            card(
                "Typed arguments",
                h.code("int"),
                ", ",
                h.code("Path"),
                ", ",
                h.code("Literal"),
                ", ",
                h.code("list[str]"),
                " — parsed, validated and documented from the signature alone.",
            ),
            card(
                "No quoting hazard",
                h.code("sh()"),
                " takes an argv list. Shell is opt-in, through ",
                h.code("sh.pipe()"),
                " and ",
                h.code("sh.bash()"),
                ", because it is the hazard.",
            ),
            card(
                "A dry run that is dry",
                h.code("--dry-run"),
                " suppresses every command, every file write through ",
                h.code("fs"),
                ", every poll, HTTP call and process kill.",
            ),
            card(
                "Tasks you can share",
                "A task package is a uv dependency, resolved and locked — not a directory each repository ",
                h.code("git clone"),
                "d and then quietly diverged from.",
            ),
            card(
                "Namespaces, not prefixes",
                "Groups give ",
                h.code("web.start"),
                " and ",
                h.code("box.ls"),
                "; a group alias makes ",
                h.code("dx.start"),
                " the same task, and ",
                h.code("override="),
                " replaces one you inherited.",
            ),
            card(
                "Startup is a feature",
                "~30 ms to a task list, with a 150 ms budget enforced by a test. Groups import "
                "lazily so an unused one costs nothing.",
            ),
        ),
    )


def who():
    return section(
        "who",
        "Is this for you?",
        h.p(
            "You have a repository with a handful of commands worth remembering — build, test, run the "
            "dev server, cut a release, reset the database — and today they live in a ",
            h.code("scripts/"),
            " folder, a ",
            h.code("package.json"),
            ", a shell history or a wiki page. That works until one of them needs a loop, a condition, "
            "or three values that must agree, and until a second repository needs the same command and "
            "you copy it.",
            cls="max-w-3xl text-lg",
        ),
        h.p(
            "This is for that moment. A task is a Python function, so when a task outgrows one line you "
            "already have the language you need — you did not have to rewrite it to get there.",
            cls="mt-4 max-w-3xl",
        ),
        cards(
            card(
                "Worth it if…",
                "your tasks take arguments; more than one repository runs the same commands; a mistake "
                "in a task deploys, kills or deletes something; your team is polyglot and only the "
                "tasks are Python.",
            ),
            card(
                "Probably not if…",
                "your repository has one command and it is ",
                h.code("cargo test"),
                ", or you cannot have Python on the machines that run the tasks.",
            ),
            columns="sm:grid-cols-2",
        ),
        alt=True,
    )


def _reason(heading, *children, first=False):
    return [
        h.h3(
            heading,
            cls=("!mt-0 " if first else "") + "mt-8 text-lg font-semibold text-slate-900 dark:text-white",
        ),
        h.p(*children, cls="mt-2"),
    ]


def worth():
    return section(
        "worth",
        "Why it is worth it",
        h.div(
            *_reason(
                "The command line is the signature",
                "Types, defaults, required-ness, help text and completions all come from the "
                "parameters. There is no second description to keep in sync, because there is no "
                "second description.",
                first=True,
            ),
            *_reason(
                "Values stay values",
                "No interpolation step exists. A filename with a space, a commit message with a quote, "
                "a password with a ",
                h.code("$"),
                " is data and cannot become syntax — not because it was escaped, but because there is "
                "no parser downstream to escape it from. Where shell genuinely is the right tool, ",
                h.code("sh.pipe(…)"),
                " says so in the source.",
            ),
            *_reason(
                "Tasks are code, so they are testable",
                "The bugs that hurt are never in the dispatch. They are in the twelve lines that decide ",
                h.em("which"),
                " process to kill or ",
                h.em("which"),
                " file to read — and those lines are an importable function here, with a recorder that "
                "captures what a task would have run.",
            ),
            code(
                [
                    kw("def"),
                    " test_the_teardown_reaps_the_lock_holder(recorder):\n    stop()\n    ",
                    kw("assert"),
                    " recorder.commands == [[",
                    kw('"kill"'),
                    ", ",
                    kw('"-TERM"'),
                    ", ",
                    kw('"4711"'),
                    "]]",
                ],
                cls="code !mt-4",
            ),
            *_reason(
                "The dry run is real",
                h.code("--dry-run"),
                " suppresses every command, every file write, every poll, HTTP call and process signal "
                "— it does not print an expansion and then still write the file. Reads are untouched, "
                "so a dry run takes the same branches the real run does.",
            ),
            *_reason(
                "Configuration fails with instructions",
                "A shared task declares what it needs as a typed section. A missing value stops before "
                "anything runs, and the error names the field, its type, and all three places it can be "
                "set: the task file, ",
                h.code("make.toml"),
                ", or the environment.",
            ),
            *_reason(
                "Sharing is a dependency, not a copy",
                "Tasks ship as ordinary Python packages. ",
                h.code("mk --sync"),
                " writes a lock file and upgrading is a version bump in a diff — instead of a directory "
                "cloned into every repository at whatever ",
                h.code("HEAD"),
                " happened to be, quietly diverging.",
            ),
            *_reason(
                "It stays fast",
                "About 30 ms to a task list, with a 150 ms budget enforced by a test. Groups "
                "import lazily, so a task package you are not using costs nothing.",
            ),
            h.p(
                "The full reasoning, decision by decision, is in ",
                a("https://github.com/optersoft/make/blob/main/docs/design.md", "docs/design.md"),
                ". If you are coming from a ",
                h.code("justfile"),
                ", the mapping is in ",
                a("https://github.com/optersoft/make/blob/main/docs/from-just.md", "docs/from-just.md"),
                ".",
                cls="!mt-8",
            ),
            cls="max-w-3xl",
        ),
    )


def direction():
    return section(
        "direction",
        "What it costs, and where it is going",
        h.p(
            "It needs a runtime — Python 3.11+, and ",
            h.code("uv"),
            " for shared task packages — and it starts in ~30 ms rather than instantly. It is "
            "alpha: the authoring API is stable in practice, the internals still move.",
            cls="max-w-3xl text-lg",
        ),
        cards(
            card(
                "The authoring API settles first",
                h.code("@task"),
                ", ",
                h.code("sh"),
                ", ",
                h.code("fs"),
                ", ",
                h.code("config"),
                ", ",
                h.code("env"),
                " and ",
                h.code("testing"),
                " are what everything is built on. A 1.0 means those stopped moving.",
            ),
            card(
                "A runner, not a build system",
                "File targets and staleness graphs were tried and removed. Compilers already track "
                "their own inputs; a second, worse graph on top produces confident wrong answers. "
                "Tasks depend on tasks.",
            ),
            card(
                "The runner stays generic",
                "Tasks that wrap a tool belong beside that tool, as its own ",
                h.code("<project>-make"),
                " package. Nothing tool-specific ships inside ",
                h.code("mkrun"),
                ".",
            ),
            card(
                "Bodies stay Python",
                "No DSL, no template language, no configuration format that slowly grows conditionals. "
                "And startup stays under budget.",
            ),
            columns="sm:grid-cols-2",
        ),
        alt=True,
    )


def start():
    return section(
        "start",
        "Get started",
        code(
            [
                prompt("$"),
                " uv tool install mkrun          ",
                comment("# installs one command: mk"),
                "\n",
                prompt("$"),
                " cd your-repo\n",
                prompt("$"),
                " mk                             ",
                comment("# offers to create a Makefile.py"),
                "\n",
                prompt("$"),
                " mk --list                      ",
                comment("# every task, with its help"),
                "\n",
                prompt("$"),
                " mk --doctor                    ",
                comment("# where each task package resolved from"),
            ],
            cls="term max-w-3xl",
        ),
        h.p(
            "Releases are tagged in the repository and published to PyPI from CI. The full authoring API — ",
            h.code("@task"),
            ", ",
            h.code("sh"),
            ", ",
            h.code("fs"),
            ", ",
            h.code("config"),
            ", ",
            h.code("env"),
            ", ",
            h.code("testing"),
            " — is in the ",
            a("https://github.com/optersoft/make#readme", "README"),
            ". The step-by-step tutorial is on ",
            a("https://academy.optersoft.com/project/make", "academy"),
            ".",
            cls="mt-6 max-w-3xl",
        ),
        h.p(
            "mkrun is built and used by ",
            h.a(
                "Optersoft",
                href="https://optersoft.com",
                cls="underline hover:text-slate-700 dark:hover:text-slate-200",
            ),
            ". MIT OR Apache-2.0, at your choice.",
            cls="mt-6 max-w-3xl text-sm text-slate-500 dark:text-slate-400",
        ),
    )


def body():
    return [hero(), what(), who(), worth(), direction(), start()]
