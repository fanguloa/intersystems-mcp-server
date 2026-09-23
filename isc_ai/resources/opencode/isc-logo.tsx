// @ts-nocheck
/** @jsxImportSource @opentui/solid */
import type { TuiPlugin, TuiThemeCurrent } from "@opencode-ai/plugin/tui"
import { useTerminalDimensions } from "@opentui/solid"
import { createMemo } from "solid-js"

const id = "isc-logo"

// InterSystems brand colours: navy left bracket, teal right bracket.
const NAVY = "#2E3192"
const TEAL = "#00B2A9"

// Two interlocking sheared brackets (the InterSystems mark). Each row is a list of
// [text, colour] segments so the two shapes keep their own colour on one line.
const N = NAVY
const T = TEAL
const mark: Array<Array<[string, string | null]>> = [
  [["          ", null], ["▄▄▄▄▄▄▄▄▄▄", T]],
  [["      ", null], ["▄▄████████████", T]],
  [["▄▄▄▄▄▄▄▄▄▄▄▄", N], ["    ", null], ["████", T]],
  [["████████████", N], ["    ", null], ["████", T]],
  [["████", N], ["            ", null], ["████", T]],
  [["████", N], ["            ", null], ["████", T]],
  [["████", N], ["            ", null], ["████", T]],
  [["████", N], ["            ", null], ["████", T]],
  [["████", N], ["  ", null], ["▄▄▄▄▄▄▄▄▄▄████", T]],
  [["████", N], ["  ", null], ["████████████▀▀", T]],
  [["████████████", N]],
  [["████████▀▀▀▀", N]],
]

const wordmark: Array<Array<[string, string | null]>> = [
  [["██", N], ["  ", null], ["▄█████▄", T], ["  ", null], ["▄█████▄", N]],
  [["██", N], ["  ", null], ["██▄▄▄▄▄", T], ["  ", null], ["██▀  ▀▀", N]],
  [["██", N], ["  ", null], ["▀▀▀▀▀██", T], ["  ", null], ["██▄  ▄▄", N]],
  [["██", N], ["  ", null], ["▀█████▀", T], ["  ", null], ["▀█████▀", N]],
]

const tagline = "InterSystems  ·  isc-ai  ·  IRIS · TrakCare · CCR"
const compact = "ISC · isc-ai"

const Row = (props: { segments: Array<[string, string | null]>; fallback: string }) => (
  <box flexDirection="row">
    {props.segments.map(([text, color]) => (
      <text fg={color ?? props.fallback}>{text}</text>
    ))}
  </box>
)

const Logo = (props: { theme: TuiThemeCurrent }) => {
  const dim = useTerminalDimensions()
  const level = createMemo(() => {
    const term = dim()
    if (term.height >= mark.length + wordmark.length + 8 && term.width >= 56) return 2
    if (term.height >= mark.length + 6 && term.width >= 40) return 1
    return 0
  })

  return (
    <box flexDirection="column" alignItems="center">
      {level() >= 1 && mark.map((segments) => <Row segments={segments} fallback={props.theme.background} />)}
      {level() >= 2 && <text> </text>}
      {level() >= 2 && wordmark.map((segments) => <Row segments={segments} fallback={props.theme.background} />)}
      {level() >= 1 && <text> </text>}
      {level() >= 1 ? (
        <text fg={props.theme.textMuted}>{tagline}</text>
      ) : (
        <text fg={TEAL}>{compact}</text>
      )}
    </box>
  )
}

const tui: TuiPlugin = async (api) => {
  api.slots.register({
    id,
    order: 100,
    slots: {
      home_logo(ctx) {
        return <Logo theme={ctx.theme.current} />
      },
    },
  })
}

const plugin = { id, tui }
export default plugin
