import { useEffect, useState } from "react";

// Proje iskeleti: arka ucun ayakta olduğunu gösteren tek sayfa.
// Sayfalar ve yönlendirme (react-router) ön yüz görevlerinde eklenecek.
export default function App() {
  const [durum, setDurum] = useState("kontrol ediliyor…");

  useEffect(() => {
    fetch("/api/saglik")
      .then((yanit) => (yanit.ok ? "çalışıyor" : `hata (${yanit.status})`))
      .catch(() => "ulaşılamıyor; arka ucu başlatın")
      .then(setDurum);
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 640, margin: "4rem auto", padding: "0 1rem" }}>
      <h1>EserLisans</h1>
      <p>ISBN tabanlı yapay zekâ eğitim lisanslama ve telif paylaşım platformu.</p>
      <p>
        Arka uç bağlantısı: <strong>{durum}</strong>
      </p>
    </main>
  );
}
