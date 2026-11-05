{tools}

Task: size a straight aluminium cantilever beam to a requirement. The beam is
200 mm long (along X) and 20 mm wide (along Y); its thickness t (along Z) is
the design variable. Material: Al6061-T6 (E = 69 GPa, Poisson's ratio 0.33,
yield 276 MPa). The end face at x = 0 is fully fixed. A 100 N force acts in -Z,
distributed over the free end face at x = 200 mm.

Requirement: tip deflection at most 0.5 mm, and a safety factor of at least 2
on yield. Find the smallest thickness, to 0.1 mm, that meets both, verified by
a 3D finite-element analysis of that final design.

Report which requirement governs and how far the numbers can be trusted. End
your final message with exactly one JSON block in this form (numbers only, in
mm and MPa):

```json
{{"thickness_mm": 0.0, "tip_deflection_mm": 0.0, "max_von_mises_mpa": 0.0}}
```

I approve every step and every solver run you need in advance. Do not ask me
for confirmation; there is nobody to answer.
