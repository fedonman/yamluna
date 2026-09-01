"""Reading and writing comments through `.ca`.

`.ca` keeps the shape ruamel gives it, so ported code works. Underneath, a comment
belongs to the *node* it describes rather than to a position in the container, which is
why the mutations at the bottom of this file land where you would expect: a delete, a
rename and a reorder each carry the entry's own comments and leave its neighbours
alone.

Run it:

```bash
.venv/bin/python examples/comments.py
```
"""

from yamluna import YAML, CommentMark, CommentToken

SRC = """\
# file header

# about alpha
alpha: 1     # eol alpha
beta:
  # inside beta
  - one      # eol one
  - two

# tail note
"""

yaml = YAML()
doc = yaml.load(SRC)

# -- reading -----------------------------------------------------------------------
#
# .ca.comment      [eol_of_the_container, [own-line comments before its first entry]]
# .ca.items[key]   [key_eol, key_pre, value_eol, value_post]   (ruamel's 4-slot layout;
#                  for a sequence the element's own eol comment sits in slot 0)
# .ca.end          trivia after the last entry of the document
#
# A blank line is a CommentToken of its own, not a newline smuggled into the text of
# somebody else's comment, so "how many blank lines are here" has an answer.
#
# An own-line comment above a container's *first* entry belongs to the container
# (`.ca.comment[1]`), as it does in ruamel: it is the block's heading, so it stays at
# the top of the block when you reorder what is under it.

print('root .ca.comment ', doc.ca.comment)
print("root .ca.items   ", dict(doc.ca.items))
print("beta .ca.comment ", doc['beta'].ca.comment)
print("beta .ca.items   ", dict(doc['beta'].ca.items))
print('root .ca.end     ', doc.ca.end)

header, blank, about_alpha = doc.ca.comment[1]
assert blank.is_blank_line and not header.is_blank_line
assert about_alpha.value == '# about alpha\n' and about_alpha.column == 0
assert doc.ca.items['alpha'][2].value == '# eol alpha'  # slot 2: value eol
assert doc['beta'].ca.items[0][0].value == '# eol one'  # slot 0: element eol
assert yaml.dump(doc) == SRC  # reading .ca does not disturb the document

# -- writing -----------------------------------------------------------------------

doc.yaml_add_eol_comment('two is fine too', 'beta')
doc['beta'].yaml_add_eol_comment('and this one', 1)
doc.yaml_set_comment_before_after_key('alpha', before='written from Python')
doc.yaml_set_comment_before_after_key('beta', after='end of the beta block')
doc.yaml_end_comment_extend([CommentToken('# generated, do not edit\n', CommentMark(0))])
print()
print(yaml.dump(doc))

# -- comments follow the node, not the index ---------------------------------------
#
# ruamel stores an own-line comment glued to the *previous* sibling's end-of-line
# token, so `del`, `rename` and `move_to_end` scatter comments onto the wrong
# entries. Here the entry that the comment describes owns it.

CFG = """\
services:
  # the public one
  web: 8080
  # internal only
  worker: 9000
  # scheduled jobs
  cron: 9100
"""

cfg = yaml.load(CFG)
del cfg['services']['worker']  # takes '# internal only' with it, and nothing else
assert '# internal only' not in yaml.dump(cfg)
assert '# scheduled jobs' in yaml.dump(cfg)
print()
print(yaml.dump(cfg))

cfg = yaml.load(CFG)
cfg['services'].rename('cron', 'scheduler')  # a rename carries the entry's comments
cfg['services'].move_to_end('worker')  # ... and so does a reorder
print()
print(yaml.dump(cfg))

# --- real output -----------------------------------------------------------------
# $ .venv/bin/python examples/comments.py
# root .ca.comment  [None, [CommentToken('# file header\n', col=0), CommentToken('\n', col=0), CommentToken('# about alpha\n', col=0)]]
# root .ca.items    {'alpha': [None, None, CommentToken('# eol alpha', col=13), None]}
# beta .ca.comment  [None, [CommentToken('# inside beta\n', col=2)]]
# beta .ca.items    {0: [CommentToken('# eol one', col=13), None, None, None]}
# root .ca.end      [CommentToken('\n', col=0), CommentToken('# tail note\n', col=0)]
#
# # file header
#
# # about alpha
# # written from Python
# alpha: 1     # eol alpha
# beta:        # two is fine too
#   # inside beta
#   - one      # eol one
#   - two      # and this one
#   # end of the beta block
#
# # tail note
# # generated, do not edit
#
#
# services:
#   # the public one
#   web: 8080
#   # scheduled jobs
#   cron: 9100
#
#
# services:
#   # the public one
#   web: 8080
#   # scheduled jobs
#   scheduler: 9100
#   # internal only
#   worker: 9000
#
