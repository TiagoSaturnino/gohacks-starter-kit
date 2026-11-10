from src.pii import _cpf_ok, mask_pii


def test_masks_email():
    out = mask_pii("contato: carlos@example.com agora")
    assert "carlos@example.com" not in out.text
    assert "[EMAIL_MASCARADO]" in out.text
    assert out.counts == {"EMAIL": 1}


def test_masks_formatted_cpf_and_cnpj():
    out = mask_pii("cpf 123.456.789-09 cnpj 11.222.333/0001-81")
    assert "[CPF_MASCARADO]" in out.text
    assert "[CNPJ_MASCARADO]" in out.text
    assert "123.456.789-09" not in out.text


def test_unformatted_cpf_only_masked_when_checksum_is_valid():
    assert _cpf_ok("52998224725")
    assert "[CPF_MASCARADO]" in mask_pii("doc 52998224725").text
    assert not _cpf_ok("12345678901")
    assert mask_pii("pedido 12345678901").text == "pedido 12345678901"


def test_masks_phone_numbers():
    assert "[TELEFONE_MASCARADO]" in mask_pii("ligar (85) 99999-8888").text
    assert "[TELEFONE_MASCARADO]" in mask_pii("ligar +55 85 99999-8888").text


def test_card_masked_only_when_luhn_is_valid():
    assert "[CARTAO_MASCARADO]" in mask_pii("cartao 4111 1111 1111 1111").text
    assert mask_pii("cartao 4111-1111-1111-1111").counts == {"CARTAO": 1}
    assert "[CARTAO_MASCARADO]" not in mask_pii("ref 1234 5678 9012 3456").text


def test_masks_value_of_person_name_keys_only():
    text = '{"customer": {"name": "Maria Silva"}, "service_name": "checkout"}'
    out = mask_pii(text)
    assert "Maria Silva" not in out.text
    assert '"service_name": "checkout"' in out.text
    assert out.counts == {"NOME": 1}


def test_does_not_touch_operational_identifiers():
    text = (
        '[2026-11-01T14:23:10Z] seller_id 8849201 item MLB1298491 order GC-88310 '
        "timeout 30000ms usage 4012/5000"
    )
    out = mask_pii(text)
    assert out.text == text
    assert out.total == 0


def test_dados_bancarios_mascarados_mas_nf_com_serie_nao():
    from src.pii import mask_pii

    texto = ("destino ted 905/0001/4471203-9 conta 4471203-9 NF 73535-1 "
             "linha 28995217401010503090100001158617316080003002303 "
             "pix 244c83bc-a736-4fff-afa2-6e12fdcf5242 TP431502 valor 90873.80 em 2026-10-30")
    out = mask_pii(texto)
    for segredo in ("4471203-9", "289952174", "244c83bc"):
        assert segredo not in out.text
    for preservado in ("NF 73535-1", "TP431502", "90873.80", "2026-10-30"):
        assert preservado in out.text


def test_mascaras_parciais_para_slack():
    from src.pii import mascarar_cnpj, mascarar_conta

    assert mascarar_conta("4471203-9") == "****203-9"
    assert mascarar_cnpj("85.779.751/0001-00") == "85.779.***/0001-**"
