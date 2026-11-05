{tools}

Task: a CNC aluminium bracket. A 120 x 80 mm base plate (120 along X, 80
along Y), 8 mm thick, with four 10 mm diameter through bolt holes on a
90 x 50 mm pattern centred on the plate. A rib 60 mm long (along X), 5 mm
thick (along Y) and 25 mm tall stands centred on the plate's top face.

Analyse it as a linear static case in Al6061-T6: the cylindrical surfaces of
the four bolt holes are fully fixed, and a 500 N force acts downward (-Z),
distributed over the top face of the rib.

Then increase the base plate thickness to 12 mm (the rib stays 25 mm tall on
the new top face) and analyse it again.

Report the maximum displacement and maximum von Mises stress before and after,
and how far the numbers can be trusted. End your final message with exactly
one JSON block in this form (numbers only, in mm and MPa):

```json
{{"before": {{"max_displacement_mm": 0.0, "max_von_mises_mpa": 0.0}},
 "after": {{"max_displacement_mm": 0.0, "max_von_mises_mpa": 0.0}}}}
```

I approve every step and every solver run you need in advance. Do not ask me
for confirmation; there is nobody to answer.
