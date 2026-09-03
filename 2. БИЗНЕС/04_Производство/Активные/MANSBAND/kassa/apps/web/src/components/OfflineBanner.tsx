import { useEffect, useState } from "react";
import { WifiOff } from "lucide-react";

/** Баннер потери сети — в зале на LTE сразу видно, почему «висит». */
export function OfflineBanner() {
  const [offline, setOffline] = useState(
    typeof navigator !== "undefined" ? !navigator.onLine : false
  );

  useEffect(() => {
    const goOff = () => setOffline(true);
    const goOn = () => setOffline(false);
    window.addEventListener("offline", goOff);
    window.addEventListener("online", goOn);
    return () => {
      window.removeEventListener("offline", goOff);
      window.removeEventListener("online", goOn);
    };
  }, []);

  if (!offline) return null;

  return (
    <div className="sticky top-0 z-50 bg-amber-500 text-ink-950 text-[13px] font-semibold px-4 py-2 flex items-center justify-center gap-2">
      <WifiOff size={16} />
      Нет сети — данные могут не сохраниться. Проверьте Wi‑Fi / LTE.
    </div>
  );
}
