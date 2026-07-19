import QtQuick
import QtQuick.Shapes
import EX30 1.0

// The Volvo iron mark, 2021 flat construction: a ring with a gap at the
// north-east through which a chunky arrow passes, VOLVO wordmark inside.
// circleSweep / arrowProgress / wordmarkProgress drive the boot animation.
Item {
    id: mark
    property real size: 200
    property color color: "#E9E9E7"
    property real circleSweep: 360       // 0..360, scaled onto the gapped ring
    property real arrowProgress: 1.0     // 0..1 — slide + fade
    property real wordmarkProgress: 1.0  // 0..1 — inner VOLVO fade + tracking
    property bool showWordmark: true

    width: size
    height: size

    readonly property real cx: size * 0.45
    readonly property real cy: size * 0.55
    readonly property real r: size * 0.36          // ring centerline radius
    readonly property real ringW: r * 0.155
    readonly property real gapHalf: 10.5           // ring gap half-angle (deg)
    readonly property real ux: 0.70710678          // 45° NE unit vector (y-down)
    readonly property real uy: -0.70710678

    // Arrow along the NE axis, in ring-width units
    readonly property real shaftIn: r - ringW * 1.0
    readonly property real shaftOut: r + ringW * 2.1
    readonly property real headTip: r + ringW * 4.3
    readonly property real headHalfW: ringW * 1.35

    // Ring — sweeps on clockwise from the gap's edge
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            strokeColor: mark.color
            strokeWidth: mark.ringW
            fillColor: "transparent"
            capStyle: ShapePath.FlatCap
            PathAngleArc {
                centerX: mark.cx
                centerY: mark.cy
                radiusX: mark.r
                radiusY: mark.r
                startAngle: -45 + mark.gapHalf
                sweepAngle: (360 - 2 * mark.gapHalf) * mark.circleSweep / 360
            }
        }
    }

    // Arrow — shaft crosses the ring gap, filled triangular head outside
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        opacity: mark.arrowProgress
        transform: Translate {
            x: -(1 - mark.arrowProgress) * mark.size * 0.10 * mark.ux
            y: -(1 - mark.arrowProgress) * mark.size * 0.10 * mark.uy
        }

        ShapePath {
            strokeColor: mark.color
            strokeWidth: mark.ringW
            fillColor: "transparent"
            capStyle: ShapePath.FlatCap
            startX: mark.cx + mark.shaftIn * mark.ux
            startY: mark.cy + mark.shaftIn * mark.uy
            PathLine {
                x: mark.cx + mark.shaftOut * mark.ux
                y: mark.cy + mark.shaftOut * mark.uy
            }
        }

        ShapePath {
            strokeColor: "transparent"
            fillColor: mark.color
            startX: mark.cx + mark.headTip * mark.ux
            startY: mark.cy + mark.headTip * mark.uy
            PathLine {
                x: mark.cx + mark.shaftOut * mark.ux + mark.headHalfW * (-mark.uy)
                y: mark.cy + mark.shaftOut * mark.uy + mark.headHalfW * mark.ux
            }
            PathLine {
                x: mark.cx + mark.shaftOut * mark.ux - mark.headHalfW * (-mark.uy)
                y: mark.cy + mark.shaftOut * mark.uy - mark.headHalfW * mark.ux
            }
            PathLine {
                x: mark.cx + mark.headTip * mark.ux
                y: mark.cy + mark.headTip * mark.uy
            }
        }
    }

    // Inner wordmark — tracks into place as it fades in
    Text {
        visible: mark.showWordmark
        opacity: mark.wordmarkProgress
        text: "VOLVO"
        color: mark.color
        font.family: Theme.sans
        font.pixelSize: Math.max(8, mark.r * 0.36)
        font.weight: Font.Bold
        font.letterSpacing: mark.r * (0.18 - 0.08 * mark.wordmarkProgress)
        // manual centering on the circle (letterSpacing pads the last glyph)
        x: mark.cx - width / 2 + font.letterSpacing / 2
        y: mark.cy - height / 2
    }
}
