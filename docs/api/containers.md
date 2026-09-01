# Containers

A load hands back `CommentedMap` and `CommentedSeq` objects. They subclass `dict` and `list`,
so `isinstance(x, dict)`, `json.dumps(x)`, `copy.deepcopy(x)`, `pickle` and `x == {'a': 1}`
all work, and you can hang your own attributes on a node.

What they add is the YAML that a builtin has nowhere to put. Every container carries `.ca`
(its comments and blank lines), `.lc` (where it sat in the source), `.anchor`, `.tag`, `.fa`
(flow or block) and `.merge`. Those attributes come from `CommentedBase` and are not
repeated on each subclass below; the classes in the second half of this page are what they
hold.

Comment records are bound to the entry they were loaded for and never to an index, so a
record travels with its element through `insert`, `del`, `pop`, `sort`, `reverse` and slice
assignment. [Comments and blank lines](../guide/comments.md) shows that in use, and
[The document model](../internals/document-model.md) describes the store underneath.

## The five containers

::: yamluna.CommentedMap

::: yamluna.CommentedSeq

::: yamluna.CommentedSet

::: yamluna.CommentedKeyMap

::: yamluna.CommentedKeySeq

::: yamluna.CommentedBase

## What the node attributes hold

`.ca` is a `Comment`, holding `CommentToken` objects; `.lc` is a `LineCol`; `.anchor` is an
`Anchor`; `.tag` is a `Tag`; `.fa` is a `Format`. A `TaggedScalar` is the odd one out: it is a
scalar rather than a container, and it exists so a tag no registered class claims still
round-trips with its value.

::: yamluna.Comment

::: yamluna.CommentToken

::: yamluna.CommentMark

::: yamluna.LineCol

::: yamluna.Anchor

::: yamluna.Tag

::: yamluna.Format

::: yamluna.TaggedScalar
