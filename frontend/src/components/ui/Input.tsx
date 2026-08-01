import React from 'react';
import styles from './Input.module.css';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  /** Monospace value — ids, urls, config values. */
  mono?: boolean;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  function Input({ invalid, mono, className, ...rest }, ref) {
    return (
      <input
        ref={ref}
        className={[
          styles.field,
          invalid ? styles.invalid : '',
          mono ? styles.mono : '',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
        {...rest}
      />
    );
  },
);

export interface TextAreaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
  /** Monospace value — commands, paths, ids, and structured line lists. */
  mono?: boolean;
}

export const TextArea = React.forwardRef<HTMLTextAreaElement, TextAreaProps>(
  function TextArea({ invalid, mono, className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={[styles.field, styles.textarea, mono ? styles.mono : '', invalid ? styles.invalid : '', className]
          .filter(Boolean)
          .join(' ')}
        {...rest}
      />
    );
  },
);
