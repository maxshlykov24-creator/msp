import { useEffect, useRef, useState } from "react";
import { X, ScanLine } from "lucide-react";

// Модалка сканера штрихкода через камеру устройства. @zxing/browser грузится
// лениво (динамический импорт), чтобы не раздувать основной бандл — камера
// нужна редко, а библиотека декодера довольно тяжёлая.

export function BarcodeScannerModal({
  onDetected,
  onClose,
}: {
  onDetected: (code: string) => void;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let controls: { stop: () => void } | undefined;
    let stopped = false;
    let cancelled = false;

    (async () => {
      try {
        const { BrowserMultiFormatReader } = await import("@zxing/browser");
        const reader = new BrowserMultiFormatReader();
        if (cancelled || !videoRef.current) return;
        await reader.decodeFromVideoDevice(undefined, videoRef.current, (result, _err, ctl) => {
          controls = ctl;
          if (result && !stopped) {
            stopped = true;
            ctl.stop();
            onDetected(result.getText());
          }
        });
      } catch {
        if (!cancelled) setError("Не удалось получить доступ к камере. Проверьте разрешения браузера.");
      }
    })();

    return () => {
      cancelled = true;
      controls?.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 z-50 bg-black/90 flex flex-col items-center justify-center p-4">
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-white/80 hover:text-white"
        aria-label="Закрыть сканер"
      >
        <X size={28} />
      </button>
      <div className="flex items-center gap-2 text-white font-semibold mb-3">
        <ScanLine size={18} className="text-gold" /> Наведите камеру на штрихкод
      </div>
      {error ? (
        <div className="text-amber-300 text-sm max-w-xs text-center">{error}</div>
      ) : (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <video ref={videoRef} className="w-full max-w-md rounded-lg border border-white/20" muted />
      )}
      <button
        onClick={onClose}
        className="mt-6 text-mute hover:text-white text-sm underline underline-offset-2"
      >
        Отмена
      </button>
    </div>
  );
}
