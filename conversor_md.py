"""
Conversor a Markdown para IA
Arrastra un documento, pulsa "Convertir" y se guarda un .md junto al original.

Requisitos:
    pip install firecrawl-anydoc PySide6
"""

import sys
from pathlib import Path

import anydoc
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

EXTENSIONES = {
    ".doc", ".docx", ".docm",
    ".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm",
    ".xls", ".xlsx", ".xlsm", ".xlsb",
    ".odt", ".ods", ".odp",
    ".rtf", ".epub", ".csv", ".pdf",
}

FILTRO_DIALOGO = "Documentos (" + " ".join(f"*{e}" for e in sorted(EXTENSIONES)) + ")"


def ruta_salida(origen: Path) -> Path:
    """informe.docx -> informe.md; si ya existe, informe (1).md, informe (2).md..."""
    destino = origen.with_suffix(".md")
    n = 1
    while destino.exists():
        destino = origen.with_name(f"{origen.stem} ({n}).md")
        n += 1
    return destino


def formatear_tamano(num_bytes: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unidad}" if unidad == "B" else f"{num_bytes:.1f} {unidad}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


# ---------------------------------------------------------------------------
# Conversión en segundo plano (anydoc libera el GIL, así la UI no se congela)
# ---------------------------------------------------------------------------
class HiloConversion(QThread):
    terminado = Signal(str, str)  # (ruta destino, markdown)
    fallido = Signal(str)         # mensaje de error

    def __init__(self, origen: Path):
        super().__init__()
        self.origen = origen

    def run(self):
        try:
            markdown = anydoc.to_markdown(self.origen)  # ocr="reject": todo en local
            destino = ruta_salida(self.origen)
            destino.write_text(markdown, encoding="utf-8")
            self.terminado.emit(str(destino), markdown)
        except anydoc.NeedsOcrError as e:
            paginas = getattr(e, "pages", None)
            detalle = f" (páginas {', '.join(map(str, paginas))})" if paginas else ""
            self.fallido.emit(
                f"El PDF tiene páginas escaneadas{detalle}. "
                "Hace falta OCR para extraer ese texto."
            )
        except anydoc.EncryptedError:
            self.fallido.emit("El archivo está protegido con contraseña. Quítala y vuelve a probar.")
        except anydoc.UnsupportedError:
            self.fallido.emit("Este formato no se puede convertir.")
        except anydoc.ConvertError as e:
            self.fallido.emit(f"No se pudo convertir: {e}")
        except OSError as e:
            self.fallido.emit(f"No se pudo leer o guardar el archivo: {e.strerror or e}")


# ---------------------------------------------------------------------------
# Zona de arrastre
# ---------------------------------------------------------------------------
class ZonaArrastre(QFrame):
    archivo_soltado = Signal(Path)

    def __init__(self):
        super().__init__()
        self.setObjectName("zona")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(190)

        self.icono = QLabel("⇣")
        self.icono.setObjectName("zonaIcono")
        self.texto = QLabel("Suelta aquí tu documento")
        self.texto.setObjectName("zonaTexto")
        self.subtexto = QLabel("o haz clic para buscarlo")
        self.subtexto.setObjectName("zonaSubtexto")

        layout = QVBoxLayout(self)
        layout.addStretch()
        for w in (self.icono, self.texto, self.subtexto):
            w.setAlignment(Qt.AlignCenter)
            layout.addWidget(w)
        layout.addStretch()

    def _set_activa(self, activa: bool):
        self.setProperty("activa", activa)
        self.style().unpolish(self)
        self.style().polish(self)

    def mostrar_archivo(self, ruta: Path):
        self.icono.setText("📄")
        self.texto.setText(ruta.name)
        self.subtexto.setText(f"{formatear_tamano(ruta.stat().st_size)}  ·  suelta otro para cambiarlo")

    # --- eventos de drag & drop ---
    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self._set_activa(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_activa(False)

    def dropEvent(self, event):
        self._set_activa(False)
        ruta = Path(event.mimeData().urls()[0].toLocalFile())
        self.archivo_soltado.emit(ruta)
        event.acceptProposedAction()

    def mousePressEvent(self, event):
        ruta, _ = QFileDialog.getOpenFileName(self, "Elegir documento", "", FILTRO_DIALOGO)
        if ruta:
            self.archivo_soltado.emit(Path(ruta))


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------
class Ventana(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Conversor a Markdown")
        self.setFixedSize(480, 460)
        self.archivo: Path | None = None
        self.destino: Path | None = None
        self.markdown = ""
        self.hilo: HiloConversion | None = None

        titulo = QLabel("Documento a Markdown")
        titulo.setObjectName("titulo")
        descripcion = QLabel("Word, PowerPoint, Excel, PDF, EPUB y más. Todo se procesa en tu equipo.")
        descripcion.setObjectName("descripcion")
        descripcion.setWordWrap(True)

        self.zona = ZonaArrastre()
        self.zona.archivo_soltado.connect(self.cargar_archivo)

        self.boton_convertir = QPushButton("Convertir")
        self.boton_convertir.setObjectName("convertir")
        self.boton_convertir.setCursor(Qt.PointingHandCursor)
        self.boton_convertir.clicked.connect(self.convertir)
        self.boton_convertir.hide()

        self.estado = QLabel("")
        self.estado.setObjectName("estado")
        self.estado.setWordWrap(True)
        self.estado.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.boton_copiar = QPushButton("Copiar Markdown")
        self.boton_carpeta = QPushButton("Abrir carpeta")
        for b in (self.boton_copiar, self.boton_carpeta):
            b.setObjectName("secundario")
            b.setCursor(Qt.PointingHandCursor)
            b.hide()
        self.boton_copiar.clicked.connect(self.copiar)
        self.boton_carpeta.clicked.connect(self.abrir_carpeta)

        fila_acciones = QHBoxLayout()
        fila_acciones.addWidget(self.boton_copiar)
        fila_acciones.addWidget(self.boton_carpeta)
        fila_acciones.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(titulo)
        layout.addWidget(descripcion)
        layout.addSpacing(6)
        layout.addWidget(self.zona, stretch=1)
        layout.addWidget(self.boton_convertir)
        layout.addWidget(self.estado)
        layout.addLayout(fila_acciones)

    # --- flujo ---
    def cargar_archivo(self, ruta: Path):
        if self.hilo and self.hilo.isRunning():
            return
        self._ocultar_resultado()

        if not ruta.is_file():
            self._mostrar_estado("Eso no es un archivo. Arrastra un documento.", "error")
            return
        if ruta.suffix.lower() not in EXTENSIONES:
            self._mostrar_estado(
                f"Los archivos {ruta.suffix or 'sin extensión'} no están soportados.", "error"
            )
            return

        self.archivo = ruta
        self.zona.mostrar_archivo(ruta)
        self.boton_convertir.setText("Convertir")
        self.boton_convertir.setEnabled(True)
        self.boton_convertir.show()
        self._mostrar_estado("", "")

    def convertir(self):
        if not self.archivo:
            return
        self.boton_convertir.setEnabled(False)
        self.boton_convertir.setText("Convirtiendo…")
        self.zona.setAcceptDrops(False)

        self.hilo = HiloConversion(self.archivo)
        self.hilo.terminado.connect(self._conversion_ok)
        self.hilo.fallido.connect(self._conversion_error)
        self.hilo.finished.connect(lambda: self.zona.setAcceptDrops(True))
        self.hilo.start()

    def _conversion_ok(self, destino: str, markdown: str):
        self.destino = Path(destino)
        self.markdown = markdown
        self.boton_convertir.setText("Convertido ✓")
        palabras = len(markdown.split())
        self._mostrar_estado(f"Guardado como {self.destino.name}  ({palabras:,} palabras)".replace(",", "."), "ok")
        self.boton_copiar.show()
        self.boton_carpeta.show()

    def _conversion_error(self, mensaje: str):
        self.boton_convertir.setText("Convertir")
        self.boton_convertir.setEnabled(True)
        self._mostrar_estado(mensaje, "error")

    # --- acciones secundarias ---
    def copiar(self):
        QGuiApplication.clipboard().setText(self.markdown)
        self.boton_copiar.setText("Copiado ✓")

    def abrir_carpeta(self):
        if self.destino:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.destino.parent)))

    # --- utilidades de UI ---
    def _ocultar_resultado(self):
        self.boton_copiar.setText("Copiar Markdown")
        self.boton_copiar.hide()
        self.boton_carpeta.hide()

    def _mostrar_estado(self, texto: str, tipo: str):
        self.estado.setText(texto)
        self.estado.setProperty("tipo", tipo)
        self.estado.style().unpolish(self.estado)
        self.estado.style().polish(self.estado)


ESTILOS = """
QWidget { background: #F7F8FA; color: #1F2933; font-size: 14px; }
#titulo { font-size: 22px; font-weight: 600; }
#descripcion { color: #616E7C; }

#zona {
    background: #FFFFFF;
    border: 2px dashed #CBD2D9;
    border-radius: 14px;
}
#zona[activa="true"] { border-color: #1E9E5A; background: #EEF9F2; }
#zona QLabel { background: transparent; }
#zonaIcono { font-size: 34px; color: #9AA5B1; }
#zonaTexto { font-size: 16px; font-weight: 600; }
#zonaSubtexto { color: #7B8794; font-size: 13px; }

#convertir {
    background: #1E9E5A; color: white;
    font-size: 16px; font-weight: 600;
    border: none; border-radius: 10px;
    padding: 12px;
}
#convertir:hover { background: #178049; }
#convertir:pressed { background: #116B3C; }
#convertir:disabled { background: #7CC9A0; }

#estado[tipo="ok"] { color: #178049; }
#estado[tipo="error"] { color: #C0392B; }

#secundario {
    background: #FFFFFF; border: 1px solid #CBD2D9;
    border-radius: 8px; padding: 6px 14px;
}
#secundario:hover { border-color: #1E9E5A; }
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(ESTILOS)
    ventana = Ventana()
    ventana.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
