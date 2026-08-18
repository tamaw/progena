import { useMemo } from 'react';
import { useSelector } from 'react-redux';
import Select from '../common/Select';
import { workflowReferenceOptions } from '../../store/uiData/workflowReferences';

/** A schema-keyed picker over a whitelisted Redux collection. */
export default function WorkflowReferencePicker({ source, value, onChange, onOptionChange, label }) {
  const options = useSelector((state) => workflowReferenceOptions(state, source));
  const selectableOptions = useMemo(
    () => options.filter((option) => !option.disabled),
    [options],
  );
  const displayOptions = useMemo(() => {
    if (!value || options.some((option) => option.value === value)) {
      return selectableOptions;
    }
    return [
      { value, label: `${value} (unavailable)`, description: 'The referenced item no longer exists.', disabled: true },
      ...selectableOptions,
    ];
  }, [options, selectableOptions, value]);
  return (
    <label className="workflow-graph-node__field">
      <span>{label}</span>
      <Select
        className="nodrag"
        value={value || ''}
        onChange={(nextValue) => {
          onChange(nextValue);
          onOptionChange?.(selectableOptions.find((option) => option.value === nextValue) || null);
        }}
        options={displayOptions}
        placeholder={`Select ${label.toLowerCase()}…`}
        searchable
        portal
        ariaLabel={label}
      />
    </label>
  );
}
