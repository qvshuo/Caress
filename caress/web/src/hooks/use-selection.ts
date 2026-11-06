import { useState } from "react";

export function useSelection() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  return {
    selected,
    toggle(keys: string[], checked: boolean) {
      setSelected((old) => {
        const next = new Set(old);
        keys.forEach((key) => (checked ? next.add(key) : next.delete(key)));
        return next;
      });
    },
    clear() {
      setSelected(new Set());
    },
  };
}
