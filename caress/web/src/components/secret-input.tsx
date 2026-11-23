import { useState } from "react";
import { Input } from "@/components/ui/input";
export function SecretInput({
  value,
  configured,
  onChange,
}: {
  value?: string;
  configured: boolean;
  onChange: (value: string) => void;
}) {
  const [focused, setFocused] = useState(false);
  return (
    <Input
      type="password"
      autoComplete="new-password"
      value={value ?? (configured && !focused ? "********" : "")}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
