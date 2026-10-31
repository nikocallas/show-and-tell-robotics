"""All prompt templates (CLAUDE.md 5.6). Each is a module constant; fill with str.format."""

DISCOVER_INSTRUCTIONS = """You look at keyframes from a top-down video of a person sorting objects on a table into \
bowls. List every distinct kind of object on the table (not the bowls, not the hand). For each kind give a short \
visual noun phrase a segmentation model can find (e.g. "red block", "small blue cube", "yellow cylinder", "glass"), \
how many there are, and its attributes using ONLY this vocabulary: {vocabulary}. A tall drinking glass is \
shape "glass", color "clear", fragile true. Blocks and cubes are shape "cube"; markers, glue sticks and caps are \
shape "cylinder". Use size "large"/"small" only when two sizes of the same shape are visible; otherwise leave size \
null. Use the name of the containers you see as container_concept (usually "bowl"). Output only JSON matching this \
schema: {schema}"""

SPEC_INSTRUCTIONS = """You convert a human demonstration and a rule sentence into a robot task spec. Rules map \
object attributes to bins. Checks must be from this closed set: in_bin, untouched, upright, unmoved. Every rule must \
have a matching check. Constraints in the sentence ('don't touch', 'leave', 'be careful with') become \
untouched/upright/unmoved checks. Infer bin assignments from where each demonstrated object ended up. If the video \
and sentence conflict, the sentence wins. If something critical is ambiguous, set clarifying_question; otherwise \
null. Output only JSON matching this schema: {schema}

Details:
- Selectors use only these attribute values: {vocabulary}. The robot's bins are left_bin, middle_bin and right_bin \
(the bowls from left to right as seen in the video).
- A rule that moves objects must not select fragile objects unless the sentence says to move them; add \
"fragile": false to such selectors when a fragile object is present.
- A rule with leave=true has no place_in; give it an untouched or unmoved check (and upright for fragile objects).
- Write each check's text as one plain sentence a person can verify by looking at the table.
- Check ids are short snake_case names, unique within the spec.
- rationale: two or three sentences on how the video and the sentence led to these rules."""

SPEC_NO_VIDEO_NOTE = """No demonstration video is available: build the spec from the sentence and the object list \
alone. When the sentence does not say which bin something goes to, choose a reasonable assignment and explain it in \
the rationale."""

PROGRAM_INSTRUCTIONS = """Write a Python function `def run(scene):` that achieves the spec using ONLY this API:
{skill_api_doc}
Select objects with scene.objects(**attrs); never use coordinates. No imports, no other functions, no while loops. \
Output JSON {{source, notes}}: source is the complete program text; notes is one or two sentences."""

REPAIR_INSTRUCTIONS = """A robot program failed some test scenes. You get: the spec, the program, the failed checks \
per seed with details, and frames from one failing scene. Diagnose the root cause in one plain sentence, then return \
a corrected program that keeps passing scenes passing. Output JSON {{diagnosis, source}}.

The program may only use this API:
{skill_api_doc}
Same rules as before: def run(scene) only, objects selected with scene.objects(**attrs), no coordinates, no imports, \
no other functions, no while loops. The event log lists what touched what and during which skill; use it."""
