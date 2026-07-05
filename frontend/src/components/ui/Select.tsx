import React from 'react';
import styles from './Input.module.css';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  options: SelectOption[];
  /** Rendered as a disabled empty-value first option. */
  placeholder?: string;
  invalid?: boolean;
  mono?: boolean;
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  function Select({ options, placeholder, invalid, mono, className, ...rest }, ref) {
    return (
      <select
        ref={ref}
        className={[
          styles.field,
          styles.select,
          invalid ? styles.invalid : '',
          mono ? styles.mono : '',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
        {...rest}
      >
        {placeholder !== undefined && (
          <option value="" disabled>
            {placeholder}
          </option>
        )}
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
    );
  },
);
