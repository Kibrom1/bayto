// Who can read an attachment: Everyone, or only the ticked roles. Shared by the add forms and the attachment list.
// `value` null means Everyone; `chosen` is true when "Only chosen roles" is picked, even before any role is ticked.
export function RolePicker({ name, legend, roles, value, chosen, onEveryone, onChoose, onToggle }: {
  name: string
  legend: string
  roles: string[] // every seat in the session (plus any role already on the attachment)
  value: string[] | null
  chosen: boolean
  onEveryone: () => void
  onChoose: () => void
  onToggle: (role: string) => void
}) {
  const restricted = !!value || chosen
  return (
    <fieldset className="context-who">
      <legend>{legend}</legend>
      <label><input type="radio" name={name} checked={!restricted} onChange={onEveryone} /> Everyone</label>
      <label><input type="radio" name={name} checked={restricted} onChange={onChoose} /> Only chosen roles</label>
      {restricted && [...new Set([...roles, ...(value ?? [])])].map((r) => (
        <label key={r} className="context-role"><input type="checkbox" checked={value?.includes(r) ?? false} onChange={() => onToggle(r)} /> {r}</label>
      ))}
      <span className="hint">Agents without access are not shown it at all, and are not told it exists. Anything an agent has already said in the discussion stays visible to the others.</span>
    </fieldset>
  )
}
