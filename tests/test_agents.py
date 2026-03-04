import pytest
from unittest.mock import Mock, patch
from agents.newsletter_agents import NewsletterAgents
from agents.llm_client import OpenRouterClient

@pytest.fixture
def sample_articles():
    return [
        {
            "title": "AI Breakthrough in Machine Learning",
            "summary": "Revolutionary new approach to neural networks",
            "url": "https://example.com/ai-breakthrough",
            "topic": "AI news",
            "content": "Detailed content about AI breakthrough"
        },
        {
            "title": "Tech Startup Raises $100M",
            "summary": "New AI startup secures major funding",
            "url": "https://example.com/startup-funding",
            "topic": "Tech startups",
            "content": "Details about startup funding"
        }
    ]

class TestOpenRouterClient:
    
    @patch('agents.llm_client.config')
    def test_client_initialization(self, mock_config):
        """Test OpenRouter client initialization"""
        mock_config.OPENROUTER_API_KEY = "test_key"
        mock_config.OPENROUTER_BASE_URL = "https://test.com"
        mock_config.DEFAULT_MODEL = "gpt-4"
        mock_config.TEMPERATURE = 0.7
        mock_config.MAX_TOKENS = 2000
        
        client = OpenRouterClient()
        
        assert client.api_key == "test_key"
        assert client.base_url == "https://test.com"
        assert client.default_model == "gpt-4"
    
    @patch('agents.llm_client.config')
    def test_client_missing_api_key(self, mock_config):
        """Test client initialization with missing API key"""
        mock_config.OPENROUTER_API_KEY = None
        
        with pytest.raises(ValueError, match="OpenRouter API key not found"):
            OpenRouterClient()
    
    @patch('requests.post')
    @patch('agents.llm_client.config')
    def test_generate_completion_success(self, mock_config, mock_post):
        """Test successful completion generation"""
        mock_config.OPENROUTER_API_KEY = "test_key"
        mock_config.OPENROUTER_BASE_URL = "https://test.com"
        mock_config.DEFAULT_MODEL = "gpt-4"
        mock_config.TEMPERATURE = 0.7
        mock_config.MAX_TOKENS = 2000
        
        # Mock successful response
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Test response"}}
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        client = OpenRouterClient()
        result = client.generate_completion("Test prompt")
        
        assert result == "Test response"
        mock_post.assert_called_once()
    
    @patch('requests.post')
    @patch('agents.llm_client.config')
    def test_generate_completion_error(self, mock_config, mock_post):
        """Test completion generation with error"""
        mock_config.OPENROUTER_API_KEY = "test_key"
        mock_config.OPENROUTER_BASE_URL = "https://test.com"
        mock_config.DEFAULT_MODEL = "gpt-4"
        mock_config.TEMPERATURE = 0.7
        mock_config.MAX_TOKENS = 2000
        
        mock_post.side_effect = Exception("API Error")
        
        client = OpenRouterClient()
        result = client.generate_completion("Test prompt")
        
        assert result == ""

class TestNewsletterAgents:
    
    @patch('agents.newsletter_agents.OpenRouterClient')
    def test_agents_initialization(self, mock_client_class):
        """Test newsletter agents initialization"""
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        agents = NewsletterAgents()
        
        assert agents.llm_client == mock_client
        assert agents.graph is not None
    
    @patch('agents.newsletter_agents.OpenRouterClient')
    def test_research_agent(self, mock_client_class, sample_articles):
        """Test research agent processing"""
        mock_client = Mock()
        mock_client.generate_completion.return_value = "Research summary content"
        mock_client_class.return_value = mock_client
        
        agents = NewsletterAgents()
        
        state = {"raw_articles": sample_articles}
        result_state = agents._research_agent(state)
        
        assert "research_summary" in result_state
        assert result_state["research_summary"] == "Research summary content"
        mock_client.generate_completion.assert_called_once()
    
    @patch('agents.newsletter_agents.OpenRouterClient')
    def test_analysis_agent(self, mock_client_class):
        """Test analysis agent processing"""
        mock_client = Mock()
        mock_client.generate_completion.return_value = "Analysis insights"
        mock_client_class.return_value = mock_client
        
        agents = NewsletterAgents()
        
        state = {"research_summary": "Test research summary"}
        result_state = agents._analysis_agent(state)
        
        assert "key_insights" in result_state
        assert result_state["key_insights"] == "Analysis insights"
    
    @patch('agents.newsletter_agents.OpenRouterClient')
    def test_opinion_agent(self, mock_client_class):
        """Test opinion agent processing"""
        mock_client = Mock()
        mock_client.generate_completion.return_value = "Opinion commentary"
        mock_client_class.return_value = mock_client
        
        agents = NewsletterAgents()
        
        state = {
            "research_summary": "Test research",
            "key_insights": "Test insights"
        }
        result_state = agents._opinion_agent(state)
        
        assert "opinion_analysis" in result_state
        assert result_state["opinion_analysis"] == "Opinion commentary"
    
    @patch('agents.newsletter_agents.OpenRouterClient')
    def test_editor_agent(self, mock_client_class):
        """Test editor agent processing"""
        mock_client = Mock()
        mock_client.generate_completion.return_value = "Final newsletter content"
        mock_client_class.return_value = mock_client
        
        agents = NewsletterAgents()
        
        state = {
            "research_summary": "Test research",
            "key_insights": "Test insights",
            "opinion_analysis": "Test opinion"
        }
        result_state = agents._editor_agent(state)
        
        assert "final_newsletter" in result_state
        assert result_state["final_newsletter"] == "Final newsletter content"
    
    @patch('agents.newsletter_agents.OpenRouterClient')
    def test_process_articles_workflow(self, mock_client_class, sample_articles):
        """Test complete article processing workflow"""
        mock_client = Mock()
        mock_client.generate_completion.side_effect = [
            "Research summary",
            "Key insights",
            "Opinion analysis",
            "Final newsletter"
        ]
        mock_client_class.return_value = mock_client
        
        agents = NewsletterAgents()
        
        # Mock the graph workflow
        with patch.object(agents, 'graph') as mock_graph:
            mock_graph.invoke.return_value = {
                "raw_articles": sample_articles,
                "research_summary": "Research summary",
                "key_insights": "Key insights",
                "opinion_analysis": "Opinion analysis",
                "final_newsletter": "Final newsletter"
            }
            
            result = agents.process_articles(sample_articles)
            
            assert "final_newsletter" in result
            assert result["final_newsletter"] == "Final newsletter"
            mock_graph.invoke.assert_called_once()

if __name__ == "__main__":
    pytest.main([__file__])