from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PIL import Image

pdf_path = "_local_uploads/sample_table_image.pdf"
c = canvas.Canvas(pdf_path, pagesize=letter)

# Load a table image (screenshot of a table)
img_path = "_local_uploads/table_screenshot.png"
c.drawImage(img_path, x=50, y=500, width=500, height=200)

c.save()
print("✅ PDF with image table generated")
