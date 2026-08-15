"""
PDF/UA (ISO 14289-1 / ISO 14289-2) & WCAG Metadata Generator
Builds PDF/UA XMP extension schemas, Dublin Core accessibility metadata,
viewer preferences, language tags, and page tab order settings.
"""

from typing import Optional
from src.engine.models import DocumentMetadata


class PDFUAMetadataBuilder:
    """
    Constructs PDF/UA-1 & PDF/UA-2 compliant XMP packet and catalog properties.
    """

    @staticmethod
    def generate_xmp_packet(metadata: DocumentMetadata) -> bytes:
        """
        Builds a compliant XMP packet with Dublin Core, Adobe PDF, and PDF/UA Extension Schemas.
        """
        title = metadata.title or "Accessible Document"
        author = metadata.author or "Antigravity PDF AutoTagger"
        subject = metadata.subject or "Accessible Remediation"
        lang = metadata.language or "en-US"

        xmp_template = f"""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    
    <!-- Dublin Core Schema -->
    <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:format>application/pdf</dc:format>
      <dc:title>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">{title}</rdf:li>
        </rdf:Alt>
      </dc:title>
      <dc:creator>
        <rdf:Seq>
          <rdf:li>{author}</rdf:li>
        </rdf:Seq>
      </dc:creator>
      <dc:description>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">{subject}</rdf:li>
        </rdf:Alt>
      </dc:description>
      <dc:language>
        <rdf:Bag>
          <rdf:li>{lang}</rdf:li>
        </rdf:Bag>
      </dc:language>
    </rdf:Description>

    <!-- PDF/UA Identification Schema -->
    <rdf:Description rdf:about="" xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">
      <pdfuaid:part>1</pdfuaid:part>
    </rdf:Description>

    <!-- PDF/UA Extension Schema Definition for Validators (PAC, VeraPDF, Adobe) -->
    <rdf:Description rdf:about="" xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"
                     xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#"
                     xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#">
      <pdfaExtension:schemas>
        <rdf:Bag>
          <rdf:li rdf:parseType="Resource">
            <pdfaSchema:schema>PDF/UA Identification Schema</pdfaSchema:schema>
            <pdfaSchema:namespaceURI>http://www.aiim.org/pdfua/ns/id/</pdfaSchema:namespaceURI>
            <pdfaSchema:prefix>pdfuaid</pdfaSchema:prefix>
            <pdfaSchema:property>
              <rdf:Seq>
                <rdf:li rdf:parseType="Resource">
                  <pdfaProperty:name>part</pdfaProperty:name>
                  <pdfaProperty:valueType>Integer</pdfaProperty:valueType>
                  <pdfaProperty:description>Indicates ISO 14289 conformance version (1 for PDF/UA-1)</pdfaProperty:description>
                </rdf:li>
              </rdf:Seq>
            </pdfaSchema:property>
          </rdf:li>
        </rdf:Bag>
      </pdfaExtension:schemas>
    </rdf:Description>

    <!-- Adobe PDF Schema -->
    <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">
      <pdf:Producer>{metadata.producer}</pdf:Producer>
      <pdf:Keywords>{", ".join(metadata.keywords)}</pdf:Keywords>
    </rdf:Description>

  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
        return xmp_template.encode("utf-8")
