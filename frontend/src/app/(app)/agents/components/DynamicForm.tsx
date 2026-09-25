'use client';

import { useState, useEffect } from 'react';
import { API_BASE } from '@/lib/api';

interface JSONSchemaProperty {
  type: string;
  title?: string;
  description?: string;
  enum?: string[];
  default?: unknown;
  items?: { type: string; format?: string };
  additionalProperties?: { type: string };
  format?: string;
  'ui:widget'?: string;
}

export interface JSONSchema {
  type: string;
  properties: Record<string, JSONSchemaProperty>;
  required?: string[];
}

interface DynamicFormProps {
  schema: JSONSchema;
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
  disabled?: boolean;
}

function toFieldValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

export default function DynamicForm({ schema, values, onChange, disabled = false }: DynamicFormProps) {
  if (!schema || !schema.properties) {
    return <p className="text-muted text-sm">No configuration fields for this type.</p>;
  }

  const requiredFields = schema.required || [];
  const properties = schema.properties;

  const handleFieldChange = (fieldName: string, value: unknown) => {
    onChange({ ...values, [fieldName]: value });
  };

  return (
    <div className="dynamic-form">
      {Object.entries(properties).map(([fieldName, prop]) => {
        const isRequired = requiredFields.includes(fieldName);
        const currentValue = values[fieldName] ?? prop.default ?? '';

        return (
          <div key={fieldName} className="form-field">
            <label className="form-label">
              {prop.title || fieldName}
              {isRequired && <span className="form-required">*</span>}
            </label>
            {prop.description && (
              <p className="form-description">{prop.description}</p>
            )}
            {renderField(fieldName, prop, currentValue, handleFieldChange, disabled)}
          </div>
        );
      })}
    </div>
  );
}

function renderField(
  fieldName: string,
  prop: JSONSchemaProperty,
  value: unknown,
  onChange: (name: string, val: unknown) => void,
  disabled: boolean
) {
  // Enum -> dropdown
  if (prop.enum) {
    return (
      <select
        className="form-input form-select"
        value={toFieldValue(value)}
        onChange={(e) => onChange(fieldName, e.target.value)}
        disabled={disabled}
      >
        <option value="">Select...</option>
        {prop.enum.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }

  // String with textarea hint
  if (prop.type === 'string' && prop['ui:widget'] === 'textarea') {
    return (
      <textarea
        className="form-input form-textarea"
        value={toFieldValue(value)}
        onChange={(e) => onChange(fieldName, e.target.value)}
        placeholder={prop.description || ''}
        rows={4}
        disabled={disabled}
      />
    );
  }

  // String with runtime selector (fetches /runtimes)
  if (prop.type === 'string' && prop['ui:widget'] === 'runtime') {
    return <RuntimeSelect value={value} onChange={(v) => onChange(fieldName, v)} disabled={disabled} />;
  }

  // Simple string
  if (prop.type === 'string') {
    return (
      <input
        type="text"
        className="form-input"
        value={toFieldValue(value)}
        onChange={(e) => onChange(fieldName, e.target.value)}
        placeholder={prop.description || ''}
        disabled={disabled}
      />
    );
  }

  // Number
  if (prop.type === 'number' || prop.type === 'integer') {
    return (
      <input
        type="number"
        className="form-input"
        value={toFieldValue(value)}
        onChange={(e) => onChange(fieldName, e.target.value ? Number(e.target.value) : '')}
        placeholder={prop.description || ''}
        disabled={disabled}
      />
    );
  }

  // Boolean -> toggle
  if (prop.type === 'boolean') {
    return (
      <label className="form-toggle-wrapper">
        <input
          type="checkbox"
          className="form-toggle"
          checked={!!value}
          onChange={(e) => onChange(fieldName, e.target.checked)}
          disabled={disabled}
        />
        <span className="form-toggle-label">{value ? 'Enabled' : 'Disabled'}</span>
      </label>
    );
  }

  // Array of strings -> multi-input
  if (prop.type === 'array' && prop.items?.type === 'string') {
    return <ArrayStringField value={value} onChange={(val) => onChange(fieldName, val)} disabled={disabled} placeholder={prop.items?.format === 'uri' ? 'https://...' : ''} />;
  }

  // Object with additionalProperties -> key-value editor
  if (prop.type === 'object' && prop.additionalProperties) {
    return <KeyValueField value={value} onChange={(val) => onChange(fieldName, val)} disabled={disabled} />;
  }

  // Fallback: text input
  return (
    <input
      type="text"
      className="form-input"
      value={toFieldValue(value)}
      onChange={(e) => onChange(fieldName, e.target.value)}
      placeholder={prop.description || ''}
      disabled={disabled}
    />
  );
}

// ───── Runtime selector component ─────
function RuntimeSelect({
  value,
  onChange,
  disabled,
}: {
  value: unknown;
  onChange: (val: string) => void;
  disabled: boolean;
}) {
  const [runtimes, setRuntimes] = useState<{ name: string; kind: string }[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/runtimes/`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => setRuntimes(Array.isArray(data) ? data : []))
      .catch(() => setRuntimes([]));
  }, []);

  return (
    <select
      className="form-input form-select"
      value={toFieldValue(value)}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
    >
      <option value="">Select runtime...</option>
      {runtimes.map((rt) => (
        <option key={rt.name} value={rt.name}>
          {rt.name}
        </option>
      ))}
    </select>
  );
}

// ───── Array of strings component ─────
function ArrayStringField({
  value,
  onChange,
  disabled,
  placeholder,
}: {
  value: unknown;
  onChange: (val: string[]) => void;
  disabled: boolean;
  placeholder?: string;
}) {
  const items: string[] = Array.isArray(value) ? value : [];

  const addItem = () => onChange([...items, '']);
  const removeItem = (idx: number) => onChange(items.filter((_, i) => i !== idx));
  const updateItem = (idx: number, val: string) => {
    const updated = [...items];
    updated[idx] = val;
    onChange(updated);
  };

  return (
    <div className="array-field">
      {items.map((item, idx) => (
        <div key={idx} className="array-field-row">
          <input
            type="text"
            className="form-input"
            value={item}
            onChange={(e) => updateItem(idx, e.target.value)}
            placeholder={placeholder}
            disabled={disabled}
          />
          <button
            type="button"
            className="array-field-remove"
            onClick={() => removeItem(idx)}
            disabled={disabled}
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn-sm btn-outline"
        onClick={addItem}
        disabled={disabled}
      >
        Add item
      </button>
    </div>
  );
}

// ───── Key-Value pairs component ─────
function KeyValueField({
  value,
  onChange,
  disabled,
}: {
  value: unknown;
  onChange: (val: Record<string, string>) => void;
  disabled: boolean;
}) {
  const record: Record<string, string> =
    value && typeof value === 'object' ? (value as Record<string, string>) : {};
  const entries: [string, string][] = Object.entries(record);

  const addPair = () => onChange({ ...record, '': '' });
  const removePair = (key: string) => {
    const updated = { ...record };
    delete updated[key];
    onChange(updated);
  };
  const updatePair = (oldKey: string, newKey: string, val: string) => {
    const updated: Record<string, string> = {};
    for (const [k, v] of Object.entries(record)) {
      if (k === oldKey) {
        updated[newKey] = val;
      } else {
        updated[k] = v;
      }
    }
    onChange(updated);
  };

  return (
    <div className="kv-field">
      {entries.map(([k, v], idx) => (
        <div key={idx} className="kv-field-row">
          <input
            type="text"
            className="form-input kv-key"
            value={k}
            onChange={(e) => updatePair(k, e.target.value, v)}
            placeholder="Key"
            disabled={disabled}
          />
          <input
            type="text"
            className="form-input kv-value"
            value={v}
            onChange={(e) => updatePair(k, k, e.target.value)}
            placeholder="Value"
            disabled={disabled}
          />
          <button
            type="button"
            className="array-field-remove"
            onClick={() => removePair(k)}
            disabled={disabled}
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn-sm btn-outline"
        onClick={addPair}
        disabled={disabled}
      >
        Add header
      </button>
    </div>
  );
}
