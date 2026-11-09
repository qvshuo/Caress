import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

export function useListState<T extends object>(
  key: string,
  initial: () => T,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const defaults = initial();
    try {
      const saved = JSON.parse(sessionStorage.getItem(`caress:${key}`) ?? "{}");
      return Object.fromEntries(
        Object.entries(defaults).map(([key, value]) => [
          key,
          Object.hasOwn(saved, key) ? saved[key] : value,
        ]),
      ) as T;
    } catch {
      return defaults;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(`caress:${key}`, JSON.stringify(value));
    } catch {
      /* List preferences remain usable when browser storage is unavailable. */
    }
  }, [key, value]);
  return [value, setValue];
}
