/** The text between U+2068 FIRST STRONG ISOLATE and U+2069 POP DIRECTIONAL ISOLATE. */
export function isolateText(text: string): string {
  // Written as escapes, so that the module holds no literal bidirectional control. The value's own U+2066 to
  // U+2069 are dropped and each paragraph separator (bidirectional type B) becomes a space: either would end
  // the pair early, and a reordering control after it would then reach the words around the value. Found by
  // code point, since lint refuses a control character in a regular expression.
  let kept = ''
  // String() is the template literal's old coercion, so a value that is not text, which a malformed body can bring, renders rather than throws.
  for (const character of Array.from(String(text))) {
    const code = character.codePointAt(0) ?? 0
    if (code >= 0x2066 && code <= 0x2069) continue
    const separator = code === 0x0a || code === 0x0d || (code >= 0x1c && code <= 0x1e) || code === 0x85 || code === 0x2029
    kept += separator ? ' ' : character
  }
  return `\u2068${kept}\u2069`
}
