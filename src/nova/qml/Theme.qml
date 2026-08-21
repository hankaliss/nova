pragma Singleton
import QtQuick

QtObject {
    // ---- Palette (Catppuccin Mocha, formalized) -------------------------
    readonly property color bg: "#181825"          // Mantle — window
    readonly property color surface: "#1e1e2e"     // Base — cards
    readonly property color surfaceAlt: "#313244"  // Surface0 — bubbles, inputs
    readonly property color border: "#45475a"      // Surface1
    readonly property color text: "#cdd6f4"        // Text — primary
    readonly property color subtext: "#a6adc8"     // Subtext1 — secondary
    readonly property color meta: "#9399b2"        // Overlay2 — meta labels (AA on Base)
    readonly property color faint: "#6c7086"       // Overlay0 — decorative only
    readonly property color disabled: "#585b70"    // Overlay2 dim
    readonly property color accent: "#89b4fa"      // Blue — primary action
    readonly property color accentText: "#11111b"  // Crust — on accent
    readonly property color ok: "#a6e3a1"          // Green
    readonly property color warn: "#f9e2af"        // Yellow
    readonly property color danger: "#f38ba8"      // Red
    readonly property color userBubble: "#89b4fa"
    readonly property color userText: "#11111b"
    readonly property color assistantBubble: "#313244"
    readonly property color assistantText: "#cdd6f4"

    // ---- Typography (pointSize — DPI-aware) ------------------------------
    readonly property real fontDisplay: 16
    readonly property real fontTitle: 11
    readonly property real fontBody: 10.5
    readonly property real fontSmall: 9
    readonly property real fontMeta: 8
    readonly property real fontMicro: 7.5
    readonly property real fontMono: 9
    readonly property string fontFamily: ""            // system font (KDE default)
    readonly property string fontMonoFamily: "Noto Sans Mono"

    // ---- Spacing (4-base scale) ------------------------------------------
    readonly property int s1: 4
    readonly property int s2: 8
    readonly property int s3: 12
    readonly property int s4: 16
    readonly property int s5: 24

    // ---- Radius -----------------------------------------------------------
    readonly property int radiusControl: 6
    readonly property int radiusCard: 10
    readonly property int radiusBubble: 14
    readonly property int radiusPill: 999

    // ---- Motion ------------------------------------------------------------
    readonly property int durationFast: 140
    readonly property int durationMedium: 200
    readonly property bool reduceMotion: false
}