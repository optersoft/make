# Ready-made consumer recipe files

One `Makefile.py` per optersoft repo, carrying over exactly what that repo's
`justfile` declared above its `import?` lines. Drop one in and it works:

```bash
cp examples/drive-Makefile.py ~/optersoft/drive/Makefile.py
cd ~/optersoft/drive && make --sync && make
```

The existing `justfile` is untouched, so `just web-start` keeps working while
`make web.start` is proven. See `../docs/migration.md` for the bridge recipes
and the group-by-group order.
