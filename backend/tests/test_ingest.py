import base64
import asyncio
from pathlib import Path
import sys

# Add parent directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.router_ingest import ingest_webhook, FileUpload


async def test_ingest_text_file():
    """Test ingesting a simple text file"""
    print("=== Test 1: Ingest Text File ===\n")
    
    # Create sample text content
    sample_text = """
    人工智能（Artificial Intelligence，AI）是计算机科学的一个分支，
    它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。
    
    该领域的研究包括机器人、语言识别、图像识别、自然语言处理和专家系统等。
    人工智能从诞生以来，理论和技术日益成熟，应用领域也不断扩大。
    
    可以设想，未来人工智能带来的科技产品，将会是人类智慧的"容器"。
    人工智能可以对人的意识、思维的信息过程的模拟。
    """
    
    # Encode to base64
    text_bytes = sample_text.encode('utf-8')
    base64_data = base64.b64encode(text_bytes).decode('utf-8')
    
    # Create FileUpload object
    file_upload = FileUpload(
        fileName="test_ai_intro.txt",
        contentType="text/plain",
        data=base64_data
    )
    
    try:
        result = await ingest_webhook(file_upload)
        print(f"✅ Text file ingestion successful!")
        print(f"   Result: {result}\n")
        return True
    except Exception as e:
        print(f"❌ Text file ingestion failed: {e}\n")
        return False


async def test_ingest_pdf_file():
    """Test ingesting a PDF file (if available)"""
    print("=== Test 2: Ingest PDF File ===\n")
    
    # Look for a sample PDF in _local_uploads or create a simple one
    pdf_path = Path(__file__).resolve().parent.parent / "_local_uploads" / "sample.pdf"
    
    if not pdf_path.exists():
        print(f"⚠️  No PDF file found at {pdf_path}")
        print(f"   Skipping PDF test\n")
        return None
    
    # Read the PDF file
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    
    # Encode to base64
    base64_data = base64.b64encode(pdf_bytes).decode('utf-8')
    
    # Create FileUpload object
    file_upload = FileUpload(
        fileName="sample.pdf",
        contentType="application/pdf",
        data=base64_data
    )
    
    try:
        result = await ingest_webhook(file_upload)
        print(f"✅ PDF file ingestion successful!")
        print(f"   Result: {result}\n")
        return True
    except Exception as e:
        print(f"❌ PDF file ingestion failed: {e}\n")
        return False


async def test_ingest_large_text():
    """Test ingesting a larger text document that will be split into multiple chunks"""
    print("=== Test 3: Ingest Large Text (Multiple Chunks) ===\n")
    
    # Create a larger text with multiple paragraphs that will be split into multiple chunks
    large_text = """
    深度学习（Deep Learning）是机器学习的一个分支，是一种以人工神经网络为架构，
    对数据进行表征学习的算法。深度学习的概念源于人工神经网络的研究。
    
    含多个隐藏层的多层感知器就是一种深度学习结构。深度学习通过组合低层特征形成更加抽象的高层表示属性类别或特征，
    以发现数据的分布式特征表示。
    
    深度学习的概念由Hinton等人于2006年提出。基于深度信念网络（DBN）提出非监督贪心逐层训练算法，
    为解决深层结构相关的优化难题带来希望，随后提出多层自动编码器深层结构。
    
    深度学习是机器学习研究中的一个新的领域，其动机在于建立、模拟人脑进行分析学习的神经网络，
    它模仿人脑的机制来解释数据，例如图像，声音和文本。
    
    深度学习的应用包括计算机视觉、语音识别、自然语言处理、音频识别与生物信息学等领域，
    并取得了极好的效果。深度学习架构如深度神经网络、卷积神经网络、深度信念网络和循环神经网络等，
    已被应用于计算机视觉、语音识别、自然语言处理、音频识别与生物信息学等领域并获得了极好的效果。
    
    在计算机视觉领域，深度学习算法已经达到甚至超过了人类的识别精度。
    在语音识别和自然语言处理领域，深度学习也显著提升了系统的性能。
    
    深度学习的未来发展将继续推动人工智能技术的进步，为人类社会带来更多创新和变革。
    随着计算能力的提升和数据量的增加，深度学习将在更多领域发挥重要作用。
    """
    
    # Encode to base64
    text_bytes = large_text.encode('utf-8')
    base64_data = base64.b64encode(text_bytes).decode('utf-8')
    
    # Create FileUpload object
    file_upload = FileUpload(
        fileName="test_deep_learning_large.txt",
        contentType="text/plain",
        data=base64_data
    )
    
    try:
        result = await ingest_webhook(file_upload)
        print(f"✅ Large text file ingestion successful!")
        print(f"   Result: {result}")
        print(f"   Text length: {len(large_text)} characters\n")
        return True
    except Exception as e:
        print(f"❌ Large text file ingestion failed: {e}\n")
        return False


async def test_ingest_unsupported_format():
    """Test ingesting an unsupported file format (should fail gracefully)"""
    print("=== Test 4: Ingest Unsupported Format ===\n")
    
    # Create sample data
    sample_data = b"Some binary data"
    base64_data = base64.b64encode(sample_data).decode('utf-8')
    
    # Create FileUpload object with unsupported content type
    file_upload = FileUpload(
        fileName="test_image.jpg",
        contentType="image/jpeg",
        data=base64_data
    )
    
    try:
        result = await ingest_webhook(file_upload)
        print(f"❌ Should have failed but succeeded: {result}\n")
        return False
    except Exception as e:
        print(f"✅ Correctly rejected unsupported format: {e}\n")
        return True


async def run_all_tests():
    """Run all ingest tests"""
    print("\n" + "="*60)
    print("        INGEST MODULE TESTING")
    print("="*60 + "\n")
    
    results = []
    
    # Run tests
    results.append(await test_ingest_text_file())
    results.append(await test_ingest_pdf_file())
    results.append(await test_ingest_large_text())
    results.append(await test_ingest_unsupported_format())
    
    # Summary
    print("\n" + "="*60)
    print("        TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if r is False)
    skipped = sum(1 for r in results if r is None)
    
    print(f"✅ Passed:  {passed}")
    print(f"❌ Failed:  {failed}")
    print(f"⚠️  Skipped: {skipped}")
    print(f"Total:     {len(results)}")
    print("="*60 + "\n")


if __name__ == "__main__":
    # Run the async tests
    asyncio.run(run_all_tests())
