export default function WorkflowNodeTitle({ expanded, title, placeholder, onChange }) {
  if (!expanded) {
    return <span className="node-title">{title || placeholder}</span>;
  }

  return (
    <input
      className="node-title-input nodrag"
      value={title}
      onChange={onChange}
      placeholder={placeholder}
    />
  );
}
