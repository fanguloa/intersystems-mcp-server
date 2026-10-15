"""isc-ai logi: a proportional image keeps the Logi default scaling mode.

Logi stretches a picture to its box when ScalingMode is "Customize", even with
Constrain_Proportion set (seen in a rendered PDF: a 1.76 logo drawn at 2.31).
"""

import unittest
import xml.etree.ElementTree as ET

from isc_ai.logigen import xmlkit


class LogiImageTests(unittest.TestCase):
    def build(self, constrain):
        parent = ET.Element("Panel")
        return xmlkit.image(parent, xmlkit.Names(), "LOGOURL", 0.0, 0.05, 1.45, 0.62, constrain=constrain)

    def test_a_constrained_image_has_no_customize_scaling(self):
        image = self.build(constrain=True)
        self.assertEqual(image.findtext("Constrain_Proportion"), "true")
        self.assertIsNone(image.find("ScalingMode"), "Customize would stretch the picture to the box")

    def test_an_unconstrained_image_fills_its_box(self):
        image = self.build(constrain=False)
        self.assertEqual(image.findtext("ScalingMode"), "Customize")
        self.assertIsNone(image.find("Constrain_Proportion"))


if __name__ == "__main__":
    unittest.main()
