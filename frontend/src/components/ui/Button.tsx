import React from 'react';
import styles from './Button.module.css';

type Variant = 'primary' | 'ghost' | 'danger' | 'icon';
type Size = 'sm' | 'md';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  /** Disables the button and swaps the label for "Working…". */
  pending?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { variant = 'ghost', size = 'md', pending, disabled, className, children, ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        className={[styles.btn, styles[variant], styles[size], className]
          .filter(Boolean)
          .join(' ')}
        disabled={disabled || pending}
        {...rest}
      >
        {pending ? 'Working…' : children}
      </button>
    );
  },
);
