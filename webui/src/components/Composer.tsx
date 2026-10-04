/* The one text composer: a rounded, auto-growing textarea with the send
   button inside. Enter sends, Shift+Enter adds a line, and Enter while an
   IME is composing (Japanese, Chinese…) is left to the IME. */

import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, type ReactNode } from "react";
import { Icon } from "./Icons";

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  placeholder: string;
  /* Accessible name of the send button. */
  submitLabel: string;
  submitIcon?: ReactNode;
  /* Send is allowed with nothing typed (e.g. an optional meeting title). */
  allowEmpty?: boolean;
  disabled?: boolean;
  /* Input stays editable but sending is blocked (an answer is streaming). */
  sendDisabled?: boolean;
  autoFocus?: boolean;
  maxLength?: number;
  /* A row under the text, inside the frame (pickers, hints). */
  footer?: ReactNode;
  className?: string;
}

export const Composer = forwardRef<HTMLTextAreaElement | null, ComposerProps>(function Composer(
  {
    value,
    onChange,
    onSubmit,
    placeholder,
    submitLabel,
    submitIcon,
    allowEmpty = false,
    disabled = false,
    sendDisabled = false,
    autoFocus,
    maxLength,
    footer,
    className,
  },
  ref,
) {
  const areaRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle<HTMLTextAreaElement | null, HTMLTextAreaElement | null>(
    ref,
    () => areaRef.current,
  );

  // Grow with the content up to the CSS max-height, then scroll.
  useLayoutEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  const canSend = !disabled && !sendDisabled && (allowEmpty || value.trim() !== "");

  const send = () => {
    if (canSend) onSubmit(value);
  };

  return (
    <form
      className={"composer" + (disabled ? " is-disabled" : "") + (className ? " " + className : "")}
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
      onClick={(e) => {
        // The whole frame reads as the input: clicks on padding focus it.
        if (e.target === e.currentTarget) areaRef.current?.focus();
      }}
    >
      <div className="composer-row">
        <textarea
          ref={areaRef}
          className="composer-input"
          rows={1}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          autoFocus={autoFocus}
          maxLength={maxLength}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          type="submit"
          className="composer-send"
          disabled={!canSend}
          aria-label={submitLabel}
          title={submitLabel}
        >
          {submitIcon ?? <Icon name="arrowUp" />}
        </button>
      </div>
      {footer && <div className="composer-footer">{footer}</div>}
    </form>
  );
});
