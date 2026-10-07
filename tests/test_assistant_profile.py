import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from assistant_profile import (
    COMPACT_PROFILE, local_system_prompt, personality_mode, request_guidance, system_prompt,
)
from dialogue import route_intent


class ProfileTests(unittest.TestCase):
    def test_profile_available_for_large_and_small_checkpoints(self):
        self.assertIn('Criação além do código', system_prompt())
        self.assertEqual(local_system_prompt(256), COMPACT_PROFILE)
        self.assertEqual(local_system_prompt(8192), system_prompt())

    def test_missing_profile_has_safe_fallback(self):
        with patch('assistant_profile.PROFILE_PATH', Path('/nonexistent/profile.md')):
            self.assertEqual(system_prompt(), COMPACT_PROFILE)

    def test_personality_layers_change_with_the_task(self):
        engineering = request_guidance('Quero criar um sistema para organizar entregas.', 'build')
        interface = request_guidance('Crie uma tela para acompanhar entregas.', 'build')
        creative = request_guidance('Escreva um conto sobre o mar.')
        conversation = request_guidance('O que você acha desta ideia?')

        self.assertIn('padrões locais seguros', engineering)
        self.assertIn('regras, dados e erros', engineering)
        self.assertIn('identidade visual', interface)
        self.assertIn('Evite dashboards e cartões genéricos', interface)
        self.assertIn('público, intenção, tom e formato', creative)
        self.assertIn('não converta automaticamente', conversation)
        self.assertIn('checkpoint neural do projeto', engineering)
        self.assertEqual(personality_mode('Como você entende uma interface?'), 'conversation')

    def test_technical_writing_routes_to_engineering(self):
        for request in ['Escreva uma API em TypeScript', 'Planeje uma biblioteca React', 'Revise este codigo Python']:
            with self.subTest(request=request):
                self.assertEqual(route_intent(request), 'programming')

    def test_creation_beyond_code(self):
        for request in ['Crie uma campanha para uma cafeteria', 'Escreva um conto sobre o mar', 'Proponha uma identidade visual', 'Sugira nomes para uma marca']:
            with self.subTest(request=request):
                self.assertEqual(route_intent(request), 'creative')


if __name__ == '__main__':
    unittest.main()
